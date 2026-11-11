"""相似艺术家「用户反馈」回归测试。

覆盖两件事：
1. 仓储层 artist_similarity_feedback 的读写（规范有序对 / UPSERT / 列表 / 清理）；
2. 服务层 find_similar_artists 对 dismissed、confirmed 的处理，
   以及「反馈表一次检测只读一次」的性能红线。

注意：find_similar_artists 的贪心 seen 语义是本文件的重点——
dismissed **不写 seen**，所以忽略 A-B 之后 A 还能跟 C 配对。
"""

import pytest

from models.db import get_db
from repository import similarity_repository as sr
from repository.artist_repository import ensure_artist
from services import similarity_service as ss


@pytest.fixture()
def artists(app_ctx):
    """造艺术家，返回 id 列表（按传入顺序）。"""

    def _make(*names, library_id=None):
        return [ensure_artist(n, library_id=library_id) for n in names]

    return _make


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """阈值是模块级配置，跑完把现场还原，别污染其他用例。"""
    saved = ss.get_thresholds()
    yield
    ss.SIMILARITY_THRESHOLDS.clear()
    ss.SIMILARITY_THRESHOLDS.update(saved)


def _pairs(groups):
    return {(g["artist_a"]["id"], g["artist_b"]["id"]) for g in groups}


# --------------------------------------------------------------------------
# 1. normalize_pair
# --------------------------------------------------------------------------


def test_normalize_pair_is_order_independent(app_ctx):
    assert sr.normalize_pair(7, 3) == (3, 7)
    assert sr.normalize_pair(3, 7) == (3, 7)
    assert sr.normalize_pair(3, 3 + 1) == (3, 4)
    # 顺序无关：两种写法规范到同一个 key
    assert sr.normalize_pair(9, 2) == sr.normalize_pair(2, 9)


def test_normalize_pair_same_id_raises(app_ctx):
    with pytest.raises(ValueError):
        sr.normalize_pair(5, 5)
    with pytest.raises(ValueError):
        sr.normalize_pair("not-an-id", 5)


# --------------------------------------------------------------------------
# 2. set_feedback：UPSERT + 非法 verdict
# --------------------------------------------------------------------------


def test_set_feedback_upsert_keeps_one_row(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")

    first = sr.set_feedback(b, a, "dismissed", reason="同名不同人")
    assert (first["artist_a_id"], first["artist_b_id"]) == (a, b)  # 已规范化
    assert first["verdict"] == "dismissed"
    assert first["reason"] == "同名不同人"

    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM artist_similarity_feedback"
    ).fetchone()
    assert row["c"] == 1

    second = sr.set_feedback(a, b, "confirmed")  # 改主意 → 覆盖
    assert second["verdict"] == "confirmed"
    assert second["updated_at"] >= first["updated_at"]

    rows = db.execute(
        "SELECT artist_a_id, artist_b_id, verdict, reason FROM artist_similarity_feedback"
    ).fetchall()
    assert len(rows) == 1  # 不产生第二行
    assert rows[0]["artist_a_id"] == a
    assert rows[0]["artist_b_id"] == b
    assert rows[0]["verdict"] == "confirmed"
    assert rows[0]["reason"] is None


def test_set_feedback_rejects_invalid_verdict(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")
    for bad in ("", "maybe", "DISMISSED", None):
        with pytest.raises(ValueError):
            sr.set_feedback(a, b, bad)
    assert sr.get_feedback_map() == {}


# --------------------------------------------------------------------------
# 3/4. dismissed / confirmed 对检测结果的影响
# --------------------------------------------------------------------------


def test_dismissed_hides_group(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")

    groups = ss.find_similar_artists()
    assert _pairs(groups) == {(a, b)}
    assert groups[0]["feedback"] is None

    sr.set_feedback(a, b, "dismissed")
    assert ss.find_similar_artists() == []


def test_confirmed_forces_group(app_ctx, artists):
    # 名称完全不相似：算法永远匹配不上，只有 confirmed 能把它捞出来
    a, b = artists("Aimer", "周杰伦")

    assert ss.find_similar_artists() == []

    sr.set_feedback(a, b, "confirmed", reason="同一人的两个马甲")
    groups = ss.find_similar_artists()
    assert _pairs(groups) == {(a, b)}
    assert groups[0]["similarity"] == 1.0
    assert groups[0]["feedback"] == "confirmed"


# --------------------------------------------------------------------------
# 5. dismissed 释放配对（本文件最关键的语义）
# --------------------------------------------------------------------------


def test_dismissed_releases_pairing(app_ctx, artists):
    """忽略 A-B 之后，A 仍然可以和 C 配对。"""
    a, b, c = artists("Aimer", "AIMER", "AIMER.")

    # 基线：贪心算法一次就吃掉了 A、B，C 落单
    baseline = ss.find_similar_artists()
    assert _pairs(baseline) == {(a, b)}

    sr.set_feedback(a, b, "dismissed")
    groups = ss.find_similar_artists()

    # A-B 不再出现，但 A 没有被连坐隐藏，转而与 C 配对
    assert _pairs(groups) == {(a, c)}
    assert all(g["feedback"] is None for g in groups)


def test_dismissed_does_not_hide_either_artist(app_ctx, artists):
    """dismissed 只跳过这一对，两个艺术家都还留在候选池里。"""
    a, b, c, d = artists("Aimer", "AIMER", "AIMER.", "AIMER..")
    sr.set_feedback(a, b, "dismissed")

    groups = ss.find_similar_artists()
    pair_set = _pairs(groups)
    assert (a, b) not in pair_set
    assert (b, a) not in pair_set
    # A 与 C 配对，B 与 D 配对（互不挤占）
    assert (a, c) in pair_set
    assert (b, d) in pair_set


# --------------------------------------------------------------------------
# 6. 性能红线：一次检测只查一次反馈表
# --------------------------------------------------------------------------


def test_feedback_map_read_only_once_per_detection(app_ctx, artists, monkeypatch):
    ids = artists(*["Aimer", "AIMER", "AIMER."])  # 至少 3 个 → 循环里有多轮两两比对
    sr.set_feedback(ids[0], ids[1], "dismissed")

    calls = {"n": 0}
    original = sr.get_feedback_map

    def counting_get_feedback_map():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(sr, "get_feedback_map", counting_get_feedback_map)

    groups = ss.find_similar_artists()
    assert groups  # 确认真的跑过比对
    assert calls["n"] == 1


# --------------------------------------------------------------------------
# 7. delete_feedback / list_feedback / prune_missing_artists
# --------------------------------------------------------------------------


def test_delete_feedback_restores_group(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")

    sr.set_feedback(a, b, "dismissed")
    assert ss.find_similar_artists() == []

    assert sr.delete_feedback(b, a) is True  # 顺序无关
    assert _pairs(ss.find_similar_artists()) == {(a, b)}

    assert sr.delete_feedback(a, b) is False  # 已不存在


def test_list_feedback_fields_and_filters(app_ctx, library, artists):
    a, b = artists("Aimer", "AIMER", library_id=library["id"])
    c, d = artists("周杰伦", "周杰倫", library_id=library["id"])

    sr.set_feedback(a, b, "dismissed", reason="不是同一人")
    sr.set_feedback(c, d, "confirmed")

    rows = sr.list_feedback()
    assert len(rows) == 2
    assert set(rows[0]) == {
        "artist_a_id",
        "artist_b_id",
        "name_a",
        "name_b",
        "verdict",
        "reason",
        "created_at",
        "updated_at",
    }
    # updated_at 倒序：confirmed 是后写的，排前面
    assert rows[0]["verdict"] == "confirmed"
    assert rows[1]["verdict"] == "dismissed"
    assert rows[1]["reason"] == "不是同一人"
    assert (rows[1]["name_a"], rows[1]["name_b"]) == ("Aimer", "AIMER")

    dismissed = sr.list_feedback(verdict="dismissed")
    assert len(dismissed) == 1
    assert dismissed[0]["verdict"] == "dismissed"

    assert len(sr.list_feedback(library_id=library["id"])) == 2
    assert sr.list_feedback(library_id=library["id"] + 999) == []

    with pytest.raises(ValueError):
        sr.list_feedback(verdict="nope")


def test_list_feedback_deleted_artist_fallback(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")
    sr.set_feedback(a, b, "dismissed")

    db = get_db()
    db.execute("DELETE FROM artists WHERE id=?", (b,))
    db.commit()

    rows = sr.list_feedback()
    assert len(rows) == 1
    assert rows[0]["name_a"] == "Aimer"
    assert rows[0]["name_b"] == "(已删除)"


def test_prune_missing_artists(app_ctx, artists):
    a, b = artists("Aimer", "AIMER")
    sr.set_feedback(a, b, "dismissed")
    # 指向不存在艺术家的悬空反馈（表上没有外键约束，遗骸是可能的）
    sr.set_feedback(99001, 99002, "dismissed")
    sr.set_feedback(99003, a, "confirmed")
    assert len(sr.list_feedback()) == 3

    assert sr.prune_missing_artists() == 2
    remaining = sr.list_feedback()
    assert len(remaining) == 1
    assert (remaining[0]["artist_a_id"], remaining[0]["artist_b_id"]) == (a, b)
    assert sr.prune_missing_artists() == 0


# --------------------------------------------------------------------------
# 8. 阈值配置
# --------------------------------------------------------------------------


def test_update_thresholds_accepts_valid_values(app_ctx):
    assert ss.get_thresholds() == {
        "short": 0.95,
        "len3": 0.85,
        "len5": 0.80,
        "long": 0.75,
    }

    updated = ss.update_thresholds({"short": 0.6, "long": 1.0})
    assert updated["short"] == 0.6
    assert updated["long"] == 1.0
    assert updated["len3"] == 0.85  # 未提及的键保持原值

    # 返回的是副本，改它不影响内部状态
    updated["short"] = 0.99
    assert ss.get_thresholds()["short"] == 0.6

    # 边界可以，整数也接受（归一成 float）
    ss.update_thresholds({"len5": 0.5, "len3": 1})
    assert ss.get_thresholds()["len5"] == 0.5
    assert isinstance(ss.get_thresholds()["len3"], float)


@pytest.mark.parametrize(
    "bad",
    [
        {"unknown": 0.9},
        {"short": 0.4},
        {"short": 1.01},
        {"short": "0.9"},
        {"short": None},
        {"short": True},
        "not-a-dict",
    ],
)
def test_update_thresholds_rejects_invalid_values(app_ctx, bad):
    before = ss.get_thresholds()
    with pytest.raises(ValueError):
        ss.update_thresholds(bad)
    assert ss.get_thresholds() == before  # 非法输入不产生副作用


def test_update_thresholds_atomic_on_partial_invalid(app_ctx):
    with pytest.raises(ValueError):
        ss.update_thresholds({"short": 0.6, "len3": 5.0})
    # 先整体校验再落盘：short 不能被改掉
    assert ss.get_thresholds()["short"] == 0.95


def test_adaptive_threshold_reads_configured_value(app_ctx, artists):
    # 2 字符 → short
    assert ss._adaptive_threshold("AB", "CD") == 0.95
    # 3 字符 → len3
    assert ss._adaptive_threshold("ABC", "DEF") == 0.85
    # 4-5 字符 → len5
    assert ss._adaptive_threshold("ABCD", "EFGH") == 0.80
    # 6+ 字符 → long
    assert ss._adaptive_threshold("ABCDEF", "GHIJKL") == 0.75

    ss.update_thresholds({"short": 0.5, "len3": 0.55, "len5": 0.6, "long": 0.65})
    assert ss._adaptive_threshold("AB", "CD") == 0.5
    assert ss._adaptive_threshold("ABC", "DEF") == 0.55
    assert ss._adaptive_threshold("ABCD", "EFGH") == 0.6
    assert ss._adaptive_threshold("ABCDEF", "GHIJKL") == 0.65


def test_threshold_change_affects_detection(app_ctx, artists):
    a, b = artists("ABCE", "ABCZ")  # 4 字符 → len5 阈值

    sim = ss.compute_similarity("ABCE", "ABCZ")
    assert 0.5 <= sim < 0.80  # 默认阈值（0.80）拦得住

    assert ss.find_similar_artists() == []

    ss.update_thresholds({"len5": 0.5})
    groups = ss.find_similar_artists()
    assert _pairs(groups) == {(a, b)}
    assert groups[0]["feedback"] is None