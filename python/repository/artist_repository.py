"""
Artist 数据访问层
封装所有与 artists 表相关的 SQL 操作
"""

import time
from models.db import get_db
from utils.metadata import normalize_str
from utils.formatting import safe_dirname


def get_artist_by_id(artist_id: int):
    db = get_db()
    return db.execute("SELECT * FROM artists WHERE id=?", (artist_id,)).fetchone()


def get_artist_by_name(name: str, library_id: int | None = None):
    db = get_db()
    name_norm = normalize_str(name)
    if library_id is not None:
        return db.execute(
            "SELECT * FROM artists WHERE name_normalized=? AND library_id=?",
            (name_norm, library_id),
        ).fetchone()
    return db.execute(
        "SELECT * FROM artists WHERE name_normalized=?", (name_norm,)
    ).fetchone()


def get_artist_by_dir_name(dir_name: str):
    db = get_db()
    return db.execute("SELECT * FROM artists WHERE dir_name=?", (dir_name,)).fetchone()


def get_all_artists(query: str | None = None, library_id: int | None = None):
    db = get_db()
    lib_filter = ""
    lib_params = []
    if library_id is not None:
        lib_filter = "AND a.library_id=?"
        lib_params.append(library_id)
    if query:
        query_norm = normalize_str(query)
        return db.execute(
            f"""
            SELECT a.*,
                   COUNT(DISTINCT al.id) AS album_count,
                   (SELECT COUNT(*) FROM tracks t WHERE t.artist_id = a.id) AS track_count,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
                   ) THEN 0 ELSE 1 END AS all_organized,
                   (SELECT MAX(t2.ctime) FROM tracks t2 WHERE t2.artist_id = a.id) AS last_created_at
            FROM artists a
            LEFT JOIN albums al ON al.artist_id = a.id
            WHERE (a.name_normalized LIKE ? OR a.name LIKE ?) {lib_filter}
            GROUP BY a.id
            ORDER BY a.name COLLATE NOCASE
        """,
            [f"%{query_norm}%", f"%{query}%"] + lib_params,
        ).fetchall()
    else:
        return db.execute(
            f"""
            SELECT a.*,
                   COUNT(DISTINCT al.id) AS album_count,
                   (SELECT COUNT(*) FROM tracks t WHERE t.artist_id = a.id) AS track_count,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
                   ) THEN 0 ELSE 1 END AS all_organized,
                   (SELECT MAX(t2.ctime) FROM tracks t2 WHERE t2.artist_id = a.id) AS last_created_at
            FROM artists a
            LEFT JOIN albums al ON al.artist_id = a.id
            WHERE 1=1 {lib_filter}
            GROUP BY a.id
            ORDER BY a.name COLLATE NOCASE
        """,
            lib_params,
        ).fetchall()


def insert_artist(
    name: str,
    dir_name: str | None = None,
    cover_path: str | None = None,
    library_id: int | None = None,
) -> int:
    db = get_db()
    name_norm = normalize_str(name)
    if not dir_name:
        dir_name = safe_dirname(name)
    now = time.time()
    cursor = db.execute(
        """
        INSERT INTO artists (name, name_normalized, dir_name, cover_path, library_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (name, name_norm, dir_name, cover_path, library_id, now, now),
    )
    db.commit()
    return cursor.lastrowid


def ensure_artist(name: str, library_id: int | None = None) -> int:
    existing = get_artist_by_name(name, library_id=library_id)
    if existing:
        if library_id is not None and existing["library_id"] is None:
            update_artist(existing["id"], library_id=library_id)
        return existing["id"]
    return insert_artist(name, library_id=library_id)


def update_artist(artist_id: int, **fields):
    if not fields:
        return
    allowed_keys = {
        "name",
        "name_normalized",
        "dir_name",
        "cover_path",
        "library_id",
        "updated_at",
        # 头像刮削状态（见 set_artist_cover_skip / record_artist_cover_*）
        "cover_skip",
        "cover_skip_reason",
        "cover_fetch_attempts",
        "cover_fetch_failed_at",
        "cover_last_source",
    }
    allowed = {k: v for k, v in fields.items() if k in allowed_keys}
    if not allowed:
        return
    allowed["updated_at"] = time.time()
    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in allowed)
    values = list(allowed.values()) + [artist_id]
    db.execute(f"UPDATE artists SET {set_clause} WHERE id=?", values)
    db.commit()


def delete_artist(artist_id: int):
    db = get_db()
    db.execute("DELETE FROM artists WHERE id=?", (artist_id,))
    db.commit()


def count_total_artists(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT COUNT(*) FROM artists WHERE library_id=?", (library_id,)
        ).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]


def count_organized_artists(library_id: int | None = None):
    db = get_db()
    lib_filter = "AND a.library_id=?" if library_id is not None else ""
    params = [library_id] if library_id is not None else []
    return db.execute(
        f"""
        SELECT COUNT(*) FROM artists a
        WHERE NOT EXISTS (
            SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
        ) {lib_filter}
    """,
        params,
    ).fetchone()[0]


def get_artist_stats(library_id: int | None = None):
    db = get_db()
    total = count_total_artists(library_id)
    lib_filter = "AND a.library_id=?" if library_id is not None else ""
    lib_params = [library_id] if library_id is not None else []
    with_cover = db.execute(
        f"SELECT COUNT(*) FROM artists a WHERE a.cover_path IS NOT NULL AND a.cover_path != '' {lib_filter}",
        lib_params,
    ).fetchone()[0]
    # 「事实计数」保持原口径：无封面数量 = 总数 - 有封面数（不排除已跳过），
    # 前端已有的展示逻辑依赖它，别改成过滤后的数字。
    without_cover = total - with_cover
    # 新口径：仍待办的无封面艺术家（cover_skip=0）与已标记「不再获取」的数量
    without_cover_active = db.execute(
        f"""
        SELECT COUNT(*) FROM artists a
        WHERE (a.cover_path IS NULL OR a.cover_path = '')
          AND COALESCE(a.cover_skip, 0) = 0 {lib_filter}
    """,
        lib_params,
    ).fetchone()[0]
    # 只统计「确实没有封面」的已标记者：这样
    # without_cover == without_cover_active + cover_skipped 恒成立，
    # 前端「另有 N 位已标记不再获取」也不会和「所有艺术家都有封面」自相矛盾。
    cover_skipped = db.execute(
        f"""
        SELECT COUNT(*) FROM artists a
        WHERE COALESCE(a.cover_skip, 0) = 1
          AND (a.cover_path IS NULL OR a.cover_path = '') {lib_filter}
    """,
        lib_params,
    ).fetchone()[0]
    organized = count_organized_artists(library_id)
    top_by_albums = db.execute(
        f"""
        SELECT a.id, a.name, a.cover_path, COUNT(DISTINCT al.id) AS album_count
        FROM artists a
        LEFT JOIN albums al ON al.artist_id = a.id
        WHERE 1=1 {lib_filter}
        GROUP BY a.id
        ORDER BY album_count DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()
    top_by_tracks = db.execute(
        f"""
        SELECT a.id, a.name, a.cover_path, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE 1=1 {lib_filter}
        GROUP BY a.id
        ORDER BY track_count DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()
    top_by_duration = db.execute(
        f"""
        SELECT a.id, a.name, a.cover_path, COALESCE(SUM(t.duration), 0) AS total_duration
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE 1=1 {lib_filter}
        GROUP BY a.id
        ORDER BY total_duration DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()
    no_cover_top = db.execute(
        f"""
        SELECT a.id, a.name, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE (a.cover_path IS NULL OR a.cover_path = '')
          AND COALESCE(a.cover_skip, 0) = 0 {lib_filter}
        GROUP BY a.id
        ORDER BY track_count DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()
    with_lyrics = db.execute(
        f"SELECT COUNT(DISTINCT artist_id) FROM tracks WHERE has_lyrics=1 AND artist_id IS NOT NULL {'AND library_id=?' if library_id is not None else ''}",
        lib_params,
    ).fetchone()[0]
    with_track_tags = db.execute(
        f"SELECT COUNT(DISTINCT artist_id) FROM tracks WHERE (missing_tags IS NULL OR missing_tags='') AND artist_id IS NOT NULL {'AND library_id=?' if library_id is not None else ''}",
        lib_params,
    ).fetchone()[0]
    return {
        "total": total,
        "with_cover": with_cover,
        "without_cover": without_cover,
        "without_cover_active": without_cover_active,
        "cover_skipped": cover_skipped,
        "organized": organized,
        "unorganized": total - organized,
        "with_lyrics": with_lyrics,
        "with_track_tags": with_track_tags,
        "top_by_albums": [dict(r) for r in top_by_albums],
        "top_by_tracks": [dict(r) for r in top_by_tracks],
        "top_by_duration": [dict(r) for r in top_by_duration],
        "no_cover_top": [dict(r) for r in no_cover_top],
    }


def get_all_artist_names(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        rows = db.execute(
            "SELECT id, name, name_normalized FROM artists WHERE library_id=? ORDER BY id",
            (library_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, name, name_normalized FROM artists ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_artists_without_cover(
    limit=10, library_id: int | None = None, include_skipped: bool = False
):
    """无封面艺术家待办列表。

    include_skipped=False（默认）时过滤掉已标记「不再自动获取头像」的艺术家；
    limit=None 表示不限制数量（返回全部）。
    """
    db = get_db()
    lib_filter = "AND a.library_id=?" if library_id is not None else ""
    params = [library_id] if library_id is not None else []
    skip_filter = "" if include_skipped else "AND COALESCE(a.cover_skip, 0) = 0"
    limit_clause = "LIMIT ?" if limit is not None else ""
    if limit is not None:
        params = params + [limit]
    rows = db.execute(
        f"""
        SELECT a.id, a.name, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE (a.cover_path IS NULL OR a.cover_path = '') {lib_filter} {skip_filter}
        GROUP BY a.id
        ORDER BY track_count DESC
        {limit_clause}
    """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def set_artist_cover_skip(
    artist_id: int, skip: bool, reason: str | None = None
) -> bool:
    """标记/解除「不再自动获取该艺术家头像」。

    skip=True  → cover_skip=1，cover_skip_reason=reason or "user"
    skip=False → cover_skip=0，cover_skip_reason=NULL（可逆）
    返回是否命中该 artist。
    """
    db = get_db()
    now = time.time()
    if skip:
        cursor = db.execute(
            "UPDATE artists SET cover_skip=1, cover_skip_reason=?, updated_at=? WHERE id=?",
            (reason or "user", now, artist_id),
        )
    else:
        cursor = db.execute(
            "UPDATE artists SET cover_skip=0, cover_skip_reason=NULL, updated_at=? WHERE id=?",
            (now, artist_id),
        )
    db.commit()
    return cursor.rowcount > 0


def record_artist_cover_failure(artist_id: int, source: str | None = None) -> None:
    """记录一次头像获取失败：累加尝试次数并写入失败时间。"""
    db = get_db()
    now = time.time()
    if source:
        db.execute(
            """
            UPDATE artists
            SET cover_fetch_attempts=COALESCE(cover_fetch_attempts, 0) + 1,
                cover_fetch_failed_at=?,
                cover_last_source=?,
                updated_at=?
            WHERE id=?
        """,
            (now, source, now, artist_id),
        )
    else:
        db.execute(
            """
            UPDATE artists
            SET cover_fetch_attempts=COALESCE(cover_fetch_attempts, 0) + 1,
                cover_fetch_failed_at=?,
                updated_at=?
            WHERE id=?
        """,
            (now, now, artist_id),
        )
    db.commit()


def record_artist_cover_success(artist_id: int, source: str | None = None) -> None:
    """记录一次头像获取成功：清零失败计数，并自动解除跳过标记。"""
    db = get_db()
    now = time.time()
    if source:
        db.execute(
            """
            UPDATE artists
            SET cover_fetch_attempts=0,
                cover_fetch_failed_at=NULL,
                cover_skip=0,
                cover_skip_reason=NULL,
                cover_last_source=?,
                updated_at=?
            WHERE id=?
        """,
            (source, now, artist_id),
        )
    else:
        db.execute(
            """
            UPDATE artists
            SET cover_fetch_attempts=0,
                cover_fetch_failed_at=NULL,
                cover_skip=0,
                cover_skip_reason=NULL,
                updated_at=?
            WHERE id=?
        """,
            (now, artist_id),
        )
    db.commit()


def get_skipped_cover_artists(library_id: int | None = None) -> list[dict]:
    """列出已标记「不再自动获取头像」的艺术家，按 name 排序。"""
    db = get_db()
    lib_filter = "AND a.library_id=?" if library_id is not None else ""
    params = [library_id] if library_id is not None else []
    rows = db.execute(
        f"""
        SELECT a.id, a.name, a.cover_skip_reason, a.cover_fetch_attempts, a.cover_fetch_failed_at
        FROM artists a
        WHERE COALESCE(a.cover_skip, 0) = 1 {lib_filter}
        ORDER BY a.name COLLATE NOCASE
    """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]
