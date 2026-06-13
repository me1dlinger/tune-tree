"""
Album 数据访问层
封装所有与 albums 表相关的 SQL 操作
"""

import time
from models.db import get_db
from utils.metadata import normalize_str
from utils.formatting import safe_dirname


def get_album_by_id(album_id: int):
    db = get_db()
    return db.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()


def get_albums_by_artist_id(artist_id: int):
    db = get_db()
    return db.execute(
        """
        SELECT al.*,
               (SELECT COUNT(*) FROM tracks t WHERE t.album_id = al.id) AS track_count,
               CASE WHEN EXISTS (
                   SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.organized=0 AND t.pending=0
               ) THEN 0 ELSE 1 END AS all_organized
        FROM albums al
        WHERE al.artist_id=?
        ORDER BY al.year, al.title COLLATE NOCASE
    """,
        (artist_id,),
    ).fetchall()


def get_album_by_title_and_artist(title: str, artist_id: int):
    db = get_db()
    title_norm = normalize_str(title)
    return db.execute(
        "SELECT * FROM albums WHERE title_normalized=? AND artist_id=?",
        (title_norm, artist_id),
    ).fetchone()


def insert_album(
    title: str,
    artist_id: int,
    dir_name: str | None = None,
    cover_path: str | None = None,
    year: str | None = None,
    library_id: int | None = None,
) -> int:
    db = get_db()
    title_norm = normalize_str(title)
    if not dir_name:
        dir_name = safe_dirname(title)
    now = time.time()
    cursor = db.execute(
        """
        INSERT INTO albums (title, title_normalized, artist_id, dir_name, cover_path, year, library_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            title,
            title_norm,
            artist_id,
            dir_name,
            cover_path,
            year,
            library_id,
            now,
            now,
        ),
    )
    db.commit()
    return cursor.lastrowid


def ensure_album(
    title: str, artist_id: int, year: str | None = None, library_id: int | None = None
) -> int:
    existing = get_album_by_title_and_artist(title, artist_id)
    if existing:
        if year and not existing["year"]:
            update_album(existing["id"], year=year)
        if library_id and not existing["library_id"]:
            update_album(existing["id"], library_id=library_id)
        return existing["id"]
    return insert_album(title, artist_id, year=year, library_id=library_id)


def update_album(album_id: int, **fields):
    if not fields:
        return
    allowed_keys = {
        "title",
        "title_normalized",
        "artist_id",
        "dir_name",
        "cover_path",
        "year",
        "library_id",
        "updated_at",
    }
    allowed = {k: v for k, v in fields.items() if k in allowed_keys}
    if not allowed:
        return
    allowed["updated_at"] = time.time()
    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in allowed)
    values = list(allowed.values()) + [album_id]
    db.execute(f"UPDATE albums SET {set_clause} WHERE id=?", values)
    db.commit()


def delete_album(album_id: int):
    db = get_db()
    db.execute("DELETE FROM albums WHERE id=?", (album_id,))
    db.commit()


def count_total_albums(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT COUNT(*) FROM albums WHERE library_id=?",
            (library_id,),
        ).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]


def count_organized_albums(library_id: int | None = None):
    db = get_db()
    lib_filter = "AND al.library_id=?" if library_id is not None else ""
    params = [library_id] if library_id is not None else []
    return db.execute(
        f"""
        SELECT COUNT(*) FROM albums al
        WHERE NOT EXISTS (
            SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.organized=0 AND t.pending=0
        ) {lib_filter}
    """,
        params,
    ).fetchone()[0]


def get_album_stats(library_id: int | None = None):
    db = get_db()
    total = count_total_albums(library_id)
    organized = count_organized_albums(library_id)
    lib_filter = "AND al.library_id=?" if library_id is not None else ""
    lib_params = [library_id] if library_id is not None else []
    tlib_filter = "AND t.library_id=?" if library_id is not None else ""

    with_cover = db.execute(
        f"SELECT COUNT(*) FROM albums al WHERE al.cover_path IS NOT NULL AND al.cover_path != '' {lib_filter}",
        lib_params,
    ).fetchone()[0]
    without_cover = total - with_cover

    with_year = db.execute(
        f"SELECT COUNT(*) FROM albums al WHERE al.year IS NOT NULL AND al.year != '' {lib_filter}",
        lib_params,
    ).fetchone()[0]

    with_track_tags = db.execute(
        f"""
        SELECT COUNT(*) FROM albums al
        WHERE NOT EXISTS (
            SELECT 1 FROM tracks t WHERE t.album_id = al.id
            AND (t.missing_tags IS NOT NULL AND t.missing_tags != '')
        ) {lib_filter}
        """,
        lib_params,
    ).fetchone()[0]

    top_by_tracks = db.execute(
        f"""
        SELECT al.id, al.title, al.cover_path, al.year, al.artist_id, ar.name AS artist_name,
               COUNT(t.id) AS track_count
        FROM albums al
        LEFT JOIN tracks t ON t.album_id = al.id
        LEFT JOIN artists ar ON ar.id = al.artist_id
        WHERE 1=1 {lib_filter}
        GROUP BY al.id
        ORDER BY track_count DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()

    top_by_duration = db.execute(
        f"""
        SELECT al.id, al.title, al.cover_path, al.year, al.artist_id, ar.name AS artist_name,
               COALESCE(SUM(t.duration), 0) AS total_duration
        FROM albums al
        LEFT JOIN tracks t ON t.album_id = al.id
        LEFT JOIN artists ar ON ar.id = al.artist_id
        WHERE 1=1 {lib_filter}
        GROUP BY al.id
        ORDER BY total_duration DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()

    top_by_size = db.execute(
        f"""
        SELECT al.id, al.title, al.cover_path, al.year, al.artist_id, ar.name AS artist_name,
               COALESCE(SUM(t.size), 0) AS total_size
        FROM albums al
        LEFT JOIN tracks t ON t.album_id = al.id
        LEFT JOIN artists ar ON ar.id = al.artist_id
        WHERE 1=1 {lib_filter}
        GROUP BY al.id
        ORDER BY total_size DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()

    no_cover_top = db.execute(
        f"""
        SELECT al.id, al.title, al.year, al.artist_id, ar.name AS artist_name, COUNT(t.id) AS track_count
        FROM albums al
        LEFT JOIN tracks t ON t.album_id = al.id
        LEFT JOIN artists ar ON ar.id = al.artist_id
        WHERE (al.cover_path IS NULL OR al.cover_path = '') {lib_filter}
        GROUP BY al.id
        ORDER BY track_count DESC
        LIMIT 10
    """,
        lib_params,
    ).fetchall()

    year_distribution = db.execute(
        f"""
        SELECT
            CASE
                WHEN al.year IS NULL OR al.year = '' THEN 'unknown'
                WHEN CAST(al.year AS INTEGER) < 1970 THEN 'before_1970'
                WHEN CAST(al.year AS INTEGER) < 1980 THEN '1970s'
                WHEN CAST(al.year AS INTEGER) < 1990 THEN '1980s'
                WHEN CAST(al.year AS INTEGER) < 2000 THEN '1990s'
                WHEN CAST(al.year AS INTEGER) < 2010 THEN '2000s'
                WHEN CAST(al.year AS INTEGER) < 2020 THEN '2010s'
                ELSE '2020s'
            END AS year_range,
            COUNT(*) AS album_count
        FROM albums al
        WHERE 1=1 {lib_filter}
        GROUP BY year_range
        ORDER BY year_range
    """,
        lib_params,
    ).fetchall()

    format_size = db.execute(
        f"""
        SELECT sub.format, sub.track_range,
               COUNT(*) AS album_count,
               AVG(sub.avg_size) AS avg_file_size
        FROM (
            SELECT al.id, t.ext AS format,
                   CASE
                       WHEN (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = al.id) <= 5 THEN '1-5'
                       WHEN (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = al.id) <= 10 THEN '6-10'
                       WHEN (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = al.id) <= 15 THEN '11-15'
                       ELSE '16+'
                   END AS track_range,
                   AVG(t.size) AS avg_size
            FROM albums al
            JOIN tracks t ON t.album_id = al.id
            WHERE 1=1 {lib_filter}
            GROUP BY al.id, t.ext
        ) sub
        GROUP BY sub.format, sub.track_range
        ORDER BY sub.format, sub.track_range
    """,
        lib_params,
    ).fetchall()

    year_distribution_by_format = db.execute(
        f"""
        SELECT
            CASE
                WHEN al.year IS NULL OR al.year = '' THEN 'unknown'
                WHEN CAST(al.year AS INTEGER) < 1970 THEN 'before_1970'
                WHEN CAST(al.year AS INTEGER) < 1980 THEN '1970s'
                WHEN CAST(al.year AS INTEGER) < 1990 THEN '1980s'
                WHEN CAST(al.year AS INTEGER) < 2000 THEN '1990s'
                WHEN CAST(al.year AS INTEGER) < 2010 THEN '2000s'
                WHEN CAST(al.year AS INTEGER) < 2020 THEN '2010s'
                ELSE '2020s'
            END AS year_range,
            t.ext AS format,
            COUNT(DISTINCT al.id) AS album_count
        FROM albums al
        JOIN tracks t ON t.album_id = al.id
        WHERE 1=1 {lib_filter}
        GROUP BY year_range, t.ext
        ORDER BY year_range, t.ext
    """,
        lib_params,
    ).fetchall()

    recent_tracks = db.execute(
        f"""
        SELECT t.id AS track_id, t.title AS track_title, t.ext, t.duration, t.size,
               t.ctime, t.track_num,
               al.id AS album_id, al.title AS album_title, al.cover_path, al.year,
               al.artist_id, ar.name AS artist_name
        FROM tracks t
        LEFT JOIN albums al ON al.id = t.album_id
        LEFT JOIN artists ar ON ar.id = al.artist_id
        WHERE t.ctime IS NOT NULL {tlib_filter}
        ORDER BY t.ctime DESC
        LIMIT 500
    """,
        lib_params,
    ).fetchall()

    return {
        "total": total,
        "with_cover": with_cover,
        "without_cover": without_cover,
        "organized": organized,
        "unorganized": total - organized,
        "with_year": with_year,
        "with_track_tags": with_track_tags,
        "top_by_tracks": [dict(r) for r in top_by_tracks],
        "top_by_duration": [dict(r) for r in top_by_duration],
        "top_by_size": [dict(r) for r in top_by_size],
        "no_cover_top": [dict(r) for r in no_cover_top],
        "year_distribution": [dict(r) for r in year_distribution],
        "year_distribution_by_format": [dict(r) for r in year_distribution_by_format],
        "format_size": [dict(r) for r in format_size],
        "recent_tracks": [dict(r) for r in recent_tracks],
    }
