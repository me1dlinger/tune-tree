"""艺术家头像「不再获取」标记的数据层测试。

覆盖四块：
1. 老库升级：``artists`` 重建分支不能丢列（最关键，丢列会静默清空用户标记）；
2. ``update_artist`` 白名单包含 5 个新列；
3. ``set_artist_cover_skip`` / ``record_artist_cover_failure|success`` 语义；
4. 待办列表与统计口径（``without_cover`` 保持事实计数不变）。
"""

import sqlite3
import time

from models.db import get_db
from repository import artist_repository as repo

NEW_COVER_COLUMNS = (
    "cover_skip",
    "cover_skip_reason",
    "cover_fetch_attempts",
    "cover_fetch_failed_at",
    "cover_last_source",
)


def _table_columns(path, table="artists"):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    finally:
        conn.close()
    return {r[1] for r in rows}


def _legacy_artists_table(db, with_cover_skip: bool = False):
    """手工造一张老 schema 的 artists 表（列级 UNIQUE 触发重建分支）。

    ``with_cover_skip=True`` 模拟「老库已经 ALTER 出 cover_skip 且用户已标记」
    的中间状态——这是重建表最容易丢数据的情形。
    """
    db.execute("DROP TABLE IF EXISTS artists")
    extra_cols = [", cover_skip INTEGER DEFAULT 0"] if with_cover_skip else []
    db.execute(
        "CREATE TABLE artists ("
        " id              INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name            TEXT NOT NULL,"
        " name_normalized TEXT NOT NULL,"
        " dir_name        TEXT NOT NULL UNIQUE,"
        " cover_path      TEXT,"
        " library_id      INTEGER,"
        " created_at      REAL NOT NULL,"
        " updated_at      REAL NOT NULL"
        + "".join(extra_cols)
        + ")"
    )
    db.commit()


def test_migration_rebuild_keeps_cover_skip(app_ctx, db_path):
    """老库（列级 UNIQUE dir_name）重建 artists 表后，标记必须原样保留。"""
    import models.db as db_module

    db = get_db()
    _legacy_artists_table(db, with_cover_skip=True)
    now = time.time()
    db.execute(
        "INSERT INTO artists (name, name_normalized, dir_name, cover_path, library_id, "
        "created_at, updated_at, cover_skip) VALUES (?,?,?,?,?,?,?,?)",
        ("老艺术家", "老艺术家", "老艺术家", None, None, now, now, 1),
    )
    db.commit()

    assert "cover_skip" in _table_columns(db_path)

    # 升级
    db_module.init_db()

    cols = _table_columns(db_path)
    missing = [c for c in NEW_COVER_COLUMNS if c not in cols]
    assert not missing, f"老库升级后丢失新列: {missing}"

    row = db.execute("SELECT name, cover_skip FROM artists").fetchone()
    assert row is not None, "重建 artists 表时数据丢失"
    assert row["name"] == "老艺术家"
    assert row["cover_skip"] == 1, "重建 artists 表时 cover_skip 被静默重置"

    # 重建后列级唯一约束应被移除（多库同名艺术家不再被拒）
    assert "sqlite_autoindex_artists_1" not in {
        r[1]
        for r in db.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND name LIKE 'sqlite_autoindex_artists_%'"
        ).fetchall()
    }


def test_migration_from_old_schema_without_new_columns(app_ctx, db_path):
    """完全不含新列的老库升级后，5 个新列都应出现且默认值为 0/NULL。"""
    import models.db as db_module

    db = get_db()
    _legacy_artists_table(db, with_cover_skip=False)
    now = time.time()
    db.execute(
        "INSERT INTO artists (name, name_normalized, dir_name, cover_path, library_id, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("远古艺术家", "远古艺术家", "远古艺术家", None, None, now, now),
    )
    db.commit()

    db_module.init_db()

    cols = _table_columns(db_path)
    missing = [c for c in NEW_COVER_COLUMNS if c not in cols]
    assert not missing, f"老库升级后丢失新列: {missing}"

    row = db.execute("SELECT cover_skip, cover_skip_reason FROM artists").fetchone()
    assert row["cover_skip"] in (0, None)
    assert row["cover_skip_reason"] is None


def test_update_artist_writes_cover_skip_fields(library):
    """update_artist 白名单必须放行 5 个新列，否则写入被静默丢弃。"""
    from repository.artist_repository import ensure_artist

    artist_id = ensure_artist("白名单测试", library_id=library["id"])
    now = time.time()
    repo.update_artist(
        artist_id,
        cover_skip=1,
        cover_skip_reason="auto_no_result",
        cover_fetch_attempts=3,
        cover_fetch_failed_at=now,
        cover_last_source="netease",
    )
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_skip"] == 1
    assert row["cover_skip_reason"] == "auto_no_result"
    assert row["cover_fetch_attempts"] == 3
    assert abs(row["cover_fetch_failed_at"] - now) < 1e-6
    assert row["cover_last_source"] == "netease"


def test_set_artist_cover_skip_roundtrip(library):
    from repository.artist_repository import ensure_artist

    artist_id = ensure_artist("跳过往返", library_id=library["id"])

    assert repo.set_artist_cover_skip(artist_id, True) is True
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_skip"] == 1
    assert row["cover_skip_reason"] == "user"  # 未给 reason 时的默认语义

    # 可逆：False 同时清空 reason
    assert repo.set_artist_cover_skip(artist_id, False) is True
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_skip"] == 0
    assert row["cover_skip_reason"] is None

    # 显式 reason
    assert repo.set_artist_cover_skip(artist_id, True, reason="auto_no_result") is True
    assert repo.get_artist_by_id(artist_id)["cover_skip_reason"] == "auto_no_result"

    # 不存在的 id
    assert repo.set_artist_cover_skip(999999, True) is False
    assert repo.set_artist_cover_skip(999999, False) is False


def test_record_failure_and_success(library):
    from repository.artist_repository import ensure_artist

    artist_id = ensure_artist("失败计数", library_id=library["id"])

    repo.record_artist_cover_failure(artist_id)
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_fetch_attempts"] == 1
    assert row["cover_fetch_failed_at"] is not None
    assert row["cover_last_source"] is None  # source 为空时不写

    repo.record_artist_cover_failure(artist_id, source="qq")
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_fetch_attempts"] == 2
    assert row["cover_last_source"] == "qq"
    failed_at = row["cover_fetch_failed_at"]

    # 成功：清零 + 自动解除跳过
    repo.set_artist_cover_skip(artist_id, True, reason="auto_no_result")
    repo.record_artist_cover_success(artist_id, source="upload")
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_fetch_attempts"] == 0
    assert row["cover_fetch_failed_at"] is None
    assert row["cover_skip"] == 0
    assert row["cover_skip_reason"] is None
    assert row["cover_last_source"] == "upload"
    assert failed_at is not None


def test_record_success_without_source_keeps_last_source(library):
    from repository.artist_repository import ensure_artist

    artist_id = ensure_artist("成功无来源", library_id=library["id"])
    repo.record_artist_cover_failure(artist_id, source="netease")
    repo.record_artist_cover_success(artist_id)  # 不带 source
    row = repo.get_artist_by_id(artist_id)
    assert row["cover_fetch_attempts"] == 0
    assert row["cover_skip"] == 0
    # 不带 source 时不应清掉历史来源（否则这条断言就是空转）
    assert row["cover_last_source"] == "netease"


def _make_artists(library):
    """A=无封面未跳过，B=无封面已跳过，C=有封面。"""
    from repository.artist_repository import ensure_artist

    a = ensure_artist("AAA 无封面待办", library_id=library["id"])
    b = ensure_artist("BBB 无封面已跳过", library_id=library["id"])
    c = ensure_artist("CCC 有封面", library_id=library["id"])
    repo.update_artist(c, cover_path="/tmp/ccc.jpg")
    repo.set_artist_cover_skip(b, True, reason="auto_no_result")
    return a, b, c


def test_get_artists_without_cover_filters_skipped(library):
    a, b, c = _make_artists(library)

    default_ids = [r["id"] for r in repo.get_artists_without_cover(limit=10)]
    assert a in default_ids
    assert b not in default_ids, "默认应排除已跳过"
    assert c not in default_ids, "有封面的不该出现"

    with_skipped = [
        r["id"] for r in repo.get_artists_without_cover(limit=10, include_skipped=True)
    ]
    assert a in with_skipped
    assert b in with_skipped, "include_skipped=True 应包含已跳过"
    assert c not in with_skipped

    # limit=None → 不限制数量：默认口径只剩未跳过的 A，include_skipped 给出 A+B
    unlimited = repo.get_artists_without_cover(limit=None)
    assert [r["id"] for r in unlimited] == [a]
    unlimited_all = repo.get_artists_without_cover(limit=None, include_skipped=True)
    assert sorted(r["id"] for r in unlimited_all) == sorted([a, b])
    # limit 生效：必须在有 2 个候选的口径下测（默认口径只剩 1 个候选，测不出 LIMIT）
    assert len(repo.get_artists_without_cover(limit=1, include_skipped=True)) == 1
    assert len(repo.get_artists_without_cover(limit=None, include_skipped=True)) == 2

    # 库过滤 + limit=None
    assert [r["id"] for r in repo.get_artists_without_cover(limit=None, library_id=library["id"])] == [a]
    assert (
        len(
            repo.get_artists_without_cover(
                limit=None, library_id=library["id"] + 999, include_skipped=True
            )
        )
        == 0
    )


def test_get_artist_stats_counts(library):
    a, b, c = _make_artists(library)

    stats = repo.get_artist_stats()
    assert stats["total"] == 3
    assert stats["with_cover"] == 1
    # 事实计数不变：无封面 = total - with_cover（含已跳过）
    assert stats["without_cover"] == 2
    assert stats["without_cover_active"] == 1
    assert stats["cover_skipped"] == 1

    no_cover_ids = [r["id"] for r in stats["no_cover_top"]]
    assert a in no_cover_ids
    assert b not in no_cover_ids, "no_cover_top 不该含已跳过"

    lib_stats = repo.get_artist_stats(library_id=library["id"])
    assert lib_stats["without_cover"] == 2
    assert lib_stats["without_cover_active"] == 1
    assert lib_stats["cover_skipped"] == 1

    other_stats = repo.get_artist_stats(library_id=library["id"] + 999)
    assert other_stats["total"] == 0
    assert other_stats["without_cover"] == 0
    assert other_stats["without_cover_active"] == 0
    assert other_stats["cover_skipped"] == 0


def test_get_skipped_cover_artists(library):
    a, b, c = _make_artists(library)

    skipped = repo.get_skipped_cover_artists()
    assert [r["id"] for r in skipped] == [b]
    assert skipped[0]["name"] == "BBB 无封面已跳过"
    assert skipped[0]["cover_skip_reason"] == "auto_no_result"
    assert set(skipped[0].keys()) == {
        "id",
        "name",
        "cover_skip_reason",
        "cover_fetch_attempts",
        "cover_fetch_failed_at",
    }

    assert repo.get_skipped_cover_artists(library_id=library["id"])[0]["id"] == b
    assert repo.get_skipped_cover_artists(library_id=library["id"] + 999) == []

    # 解除后不再出现
    repo.set_artist_cover_skip(b, False)
    assert repo.get_skipped_cover_artists() == []


def test_get_skipped_cover_artists_sorted_by_name(library):
    from repository.artist_repository import ensure_artist

    ids = []
    for name in ("zeta", "Alpha", "mid"):
        aid = ensure_artist(name, library_id=library["id"])
        repo.set_artist_cover_skip(aid, True)
        ids.append((name, aid))

    names = [r["name"] for r in repo.get_skipped_cover_artists()]
    assert names == ["Alpha", "mid", "zeta"]