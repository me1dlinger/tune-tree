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
