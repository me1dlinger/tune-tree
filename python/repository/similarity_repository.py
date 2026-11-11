"""
相似艺术家反馈数据访问层
封装 artist_similarity_feedback 表的 SQL 操作

设计要点：
- 艺术家对 **恒以 (较小 id, 较大 id) 的规范有序对** 存储，配合
  UNIQUE(artist_a_id, artist_b_id)，才能拦住「A-B」与「B-A」被记成两行。
- ``find_similar_artists`` 是 O(n²) 的两两比对，因此提供 ``get_feedback_map()``
  一次性把整表读进内存做 dict 查找；**绝不允许**在双重循环里逐对查库。
"""

import time

from models.db import get_db

# 合法判定：dismissed = 用户标记「不是同一人」；confirmed = 用户标记「是同一人」
VALID_VERDICTS = {"dismissed", "confirmed"}

# 艺术家已被删除时，列表里的名字兜底
_DELETED_NAME = "(已删除)"


def normalize_pair(artist_a_id: int, artist_b_id: int) -> tuple[int, int]:
    """把艺术家对规范成 (较小 id, 较大 id)，顺序无关。

    同一个 id 组成的一对没有意义（自己跟自己不可能是「相似艺术家」），抛 ValueError。
    """
    try:
        a, b = int(artist_a_id), int(artist_b_id)
    except (TypeError, ValueError):
        raise ValueError("artist id 必须是整数")
    if a == b:
        raise ValueError("artist_a_id 与 artist_b_id 不能相同")
    return (a, b) if a < b else (b, a)


def get_feedback_map() -> dict[tuple[int, int], str]:
    """一次性读全表 → {(artist_a_id, artist_b_id): verdict}。

    给 ``find_similar_artists`` 用：O(n²) 的比对循环里只在内存 dict 中查，
    整个检测过程只需一次数据库查询。
    """
    db = get_db()
    rows = db.execute(
        "SELECT artist_a_id, artist_b_id, verdict FROM artist_similarity_feedback"
    ).fetchall()
    return {(r["artist_a_id"], r["artist_b_id"]): r["verdict"] for r in rows}


def set_feedback(
    artist_a_id: int,
    artist_b_id: int,
    verdict: str,
    reason: str | None = None,
) -> dict:
    """写入 / 覆盖一对艺术家的反馈（UPSERT）。

    改主意（dismissed → confirmed 或反之）走 ON CONFLICT DO UPDATE 覆盖同一行，
    不会产生第二行记录。返回规范化之后的字段。
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"非法 verdict: {verdict!r}，只允许 {sorted(VALID_VERDICTS)}")
    a, b = normalize_pair(artist_a_id, artist_b_id)

    now = time.time()
    db = get_db()
    db.execute(
        """
        INSERT INTO artist_similarity_feedback
            (artist_a_id, artist_b_id, verdict, reason, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(artist_a_id, artist_b_id) DO UPDATE SET
            verdict    = excluded.verdict,
            reason     = excluded.reason,
            updated_at = excluded.updated_at
        """,
        (a, b, verdict, reason, now, now),
    )
    db.commit()
    return {
        "artist_a_id": a,
        "artist_b_id": b,
        "verdict": verdict,
        "reason": reason,
        "updated_at": now,
    }


def delete_feedback(artist_a_id: int, artist_b_id: int) -> bool:
    """撤销一对艺术家的反馈，返回是否真的删掉了一行。"""
    a, b = normalize_pair(artist_a_id, artist_b_id)
    db = get_db()
    cur = db.execute(
        "DELETE FROM artist_similarity_feedback WHERE artist_a_id=? AND artist_b_id=?",
        (a, b),
    )
    db.commit()
    return cur.rowcount > 0


def list_feedback(
    verdict: str | None = None,
    library_id: int | None = None,
) -> list[dict]:
    """列出反馈记录（按 updated_at 倒序）。

    LEFT JOIN artists 取两侧名字；艺术家已被删除时名字兜底 "(已删除)"。
    传入 library_id 时保留**任一侧**属于该音乐库的记录（正常情况下一对同库，
    跨库残留也能被看到，不至于从列表里凭空消失）。
    """
    if verdict is not None and verdict not in VALID_VERDICTS:
        raise ValueError(f"非法 verdict: {verdict!r}，只允许 {sorted(VALID_VERDICTS)}")

    sql = """
        SELECT f.artist_a_id,
               f.artist_b_id,
               COALESCE(NULLIF(a.name, ''), ?) AS name_a,
               COALESCE(NULLIF(b.name, ''), ?) AS name_b,
               f.verdict,
               f.reason,
               f.created_at,
               f.updated_at
        FROM artist_similarity_feedback f
        LEFT JOIN artists a ON a.id = f.artist_a_id
        LEFT JOIN artists b ON b.id = f.artist_b_id
        WHERE 1=1
    """
    params: list = [_DELETED_NAME, _DELETED_NAME]

    if verdict is not None:
        sql += " AND f.verdict = ?"
        params.append(verdict)

    if library_id is not None:
        sql += " AND (a.library_id = ? OR b.library_id = ?)"
        params.extend([library_id, library_id])

    sql += " ORDER BY f.updated_at DESC, f.id DESC"

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def prune_missing_artists() -> int:
    """清理指向已不存在艺术家的反馈行，返回删除条数。"""
    db = get_db()
    cur = db.execute(
        """
        DELETE FROM artist_similarity_feedback
        WHERE NOT EXISTS (
                  SELECT 1 FROM artists a
                  WHERE a.id = artist_similarity_feedback.artist_a_id
              )
           OR NOT EXISTS (
                  SELECT 1 FROM artists b
                  WHERE b.id = artist_similarity_feedback.artist_b_id
              )
        """
    )
    db.commit()
    return cur.rowcount