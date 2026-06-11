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


def get_artist_by_name(name: str):
    db = get_db()
    name_norm = normalize_str(name)
    return db.execute(
        "SELECT * FROM artists WHERE name_normalized=?", (name_norm,)
    ).fetchone()


def get_artist_by_dir_name(dir_name: str):
    db = get_db()
    return db.execute("SELECT * FROM artists WHERE dir_name=?", (dir_name,)).fetchone()


def get_all_artists(query: str | None = None):
    db = get_db()
    if query:
        query_norm = normalize_str(query)
        return db.execute(
            """
            SELECT a.*,
                   COUNT(DISTINCT al.id) AS album_count,
                   (SELECT COUNT(*) FROM tracks t WHERE t.artist_id = a.id) AS track_count,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
                   ) THEN 0 ELSE 1 END AS all_organized
            FROM artists a
            LEFT JOIN albums al ON al.artist_id = a.id
            WHERE a.name_normalized LIKE ? OR a.name LIKE ?
            GROUP BY a.id
            ORDER BY a.name COLLATE NOCASE
        """,
            (f"%{query_norm}%", f"%{query}%"),
        ).fetchall()
    else:
        return db.execute(
            """
            SELECT a.*,
                   COUNT(DISTINCT al.id) AS album_count,
                   (SELECT COUNT(*) FROM tracks t WHERE t.artist_id = a.id) AS track_count,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
                   ) THEN 0 ELSE 1 END AS all_organized
            FROM artists a
            LEFT JOIN albums al ON al.artist_id = a.id
            GROUP BY a.id
            ORDER BY a.name COLLATE NOCASE
        """
        ).fetchall()


def insert_artist(
    name: str, dir_name: str | None = None, cover_path: str | None = None
) -> int:
    db = get_db()
    name_norm = normalize_str(name)
    if not dir_name:
        dir_name = safe_dirname(name)
    now = time.time()
    cursor = db.execute(
        """
        INSERT INTO artists (name, name_normalized, dir_name, cover_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (name, name_norm, dir_name, cover_path, now, now),
    )
    db.commit()
    return cursor.lastrowid


def ensure_artist(name: str) -> int:
    existing = get_artist_by_name(name)
    if existing:
        return existing["id"]
    return insert_artist(name)


def update_artist(artist_id: int, **fields):
    if not fields:
        return
    allowed_keys = {"name", "name_normalized", "dir_name", "cover_path", "updated_at"}
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


def count_total_artists():
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]


def count_organized_artists():
    db = get_db()
    return db.execute("""
        SELECT COUNT(*) FROM artists a
        WHERE NOT EXISTS (
            SELECT 1 FROM tracks t WHERE t.artist_id = a.id AND t.organized=0 AND t.pending=0
        )
    """).fetchone()[0]


def get_artist_stats():
    db = get_db()
    total = count_total_artists()
    with_cover = db.execute(
        "SELECT COUNT(*) FROM artists WHERE cover_path IS NOT NULL AND cover_path != ''"
    ).fetchone()[0]
    without_cover = total - with_cover
    organized = count_organized_artists()
    top_by_albums = db.execute("""
        SELECT a.id, a.name, a.cover_path, COUNT(DISTINCT al.id) AS album_count
        FROM artists a
        LEFT JOIN albums al ON al.artist_id = a.id
        GROUP BY a.id
        ORDER BY album_count DESC
        LIMIT 10
    """).fetchall()
    top_by_tracks = db.execute("""
        SELECT a.id, a.name, a.cover_path, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        GROUP BY a.id
        ORDER BY track_count DESC
        LIMIT 10
    """).fetchall()
    top_by_duration = db.execute("""
        SELECT a.id, a.name, a.cover_path, COALESCE(SUM(t.duration), 0) AS total_duration
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        GROUP BY a.id
        ORDER BY total_duration DESC
        LIMIT 10
    """).fetchall()
    no_cover_top = db.execute("""
        SELECT a.id, a.name, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE a.cover_path IS NULL OR a.cover_path = ''
        GROUP BY a.id
        ORDER BY track_count DESC
        LIMIT 10
    """).fetchall()
    with_lyrics = db.execute(
        "SELECT COUNT(DISTINCT artist_id) FROM tracks WHERE has_lyrics=1 AND artist_id IS NOT NULL"
    ).fetchone()[0]
    with_track_tags = db.execute(
        "SELECT COUNT(DISTINCT artist_id) FROM tracks WHERE (missing_tags IS NULL OR missing_tags='') AND artist_id IS NOT NULL"
    ).fetchone()[0]
    return {
        "total": total,
        "with_cover": with_cover,
        "without_cover": without_cover,
        "organized": organized,
        "unorganized": total - organized,
        "with_lyrics": with_lyrics,
        "with_track_tags": with_track_tags,
        "top_by_albums": [dict(r) for r in top_by_albums],
        "top_by_tracks": [dict(r) for r in top_by_tracks],
        "top_by_duration": [dict(r) for r in top_by_duration],
        "no_cover_top": [dict(r) for r in no_cover_top],
    }


def get_all_artist_names():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, name_normalized FROM artists ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_artists_without_cover(limit=10):
    db = get_db()
    rows = db.execute(
        """
        SELECT a.id, a.name, COUNT(t.id) AS track_count
        FROM artists a
        LEFT JOIN tracks t ON t.artist_id = a.id
        WHERE a.cover_path IS NULL OR a.cover_path = ''
        GROUP BY a.id
        ORDER BY track_count DESC
        LIMIT ?
    """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
