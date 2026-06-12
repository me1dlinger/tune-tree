"""
Music Library 数据访问层
封装所有与 music_libraries 表相关的 SQL 操作
"""

import time
from models.db import get_db


def get_all_libraries():
    db = get_db()
    return db.execute(
        "SELECT * FROM music_libraries ORDER BY is_default DESC, name COLLATE NOCASE"
    ).fetchall()


def get_library_by_id(library_id: int):
    db = get_db()
    return db.execute(
        "SELECT * FROM music_libraries WHERE id=?", (library_id,)
    ).fetchone()


def get_default_library():
    db = get_db()
    return db.execute(
        "SELECT * FROM music_libraries WHERE is_default=1 LIMIT 1"
    ).fetchone()


def get_current_library_id() -> int | None:
    lib_id = get_scan_meta_value("current_library_id")
    if lib_id:
        lib = get_library_by_id(int(lib_id))
        if lib:
            return int(lib_id)
    default = get_default_library()
    if default:
        return default["id"]
    libs = get_all_libraries()
    return libs[0]["id"] if libs else None


def get_current_library():
    lib_id = get_current_library_id()
    if lib_id:
        return get_library_by_id(lib_id)
    return None


def get_current_library_path() -> str | None:
    lib = get_current_library()
    return lib["path"] if lib else None


def set_current_library_id(library_id: int):
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO scan_meta (key, value) VALUES (?, ?)",
        ("current_library_id", str(library_id)),
    )
    db.commit()


def insert_library(
    name: str, path: str, is_default: int = 0, needs_config: int = 0
) -> int:
    db = get_db()
    now = time.time()
    cursor = db.execute(
        "INSERT INTO music_libraries (name, path, is_default, needs_config, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, path, is_default, needs_config, now, now),
    )
    db.commit()
    return cursor.lastrowid


def update_library(library_id: int, **fields):
    if not fields:
        return
    allowed_keys = {"name", "path", "is_default", "needs_config", "updated_at"}
    allowed = {k: v for k, v in fields.items() if k in allowed_keys}
    if not allowed:
        return
    allowed["updated_at"] = time.time()
    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in allowed)
    values = list(allowed.values()) + [library_id]
    db.execute(f"UPDATE music_libraries SET {set_clause} WHERE id=?", values)
    db.commit()


def delete_library(library_id: int):
    db = get_db()
    artist_ids = [
        r["id"]
        for r in db.execute(
            "SELECT id FROM artists WHERE library_id=?", (library_id,)
        ).fetchall()
    ]
    if artist_ids:
        placeholders = ",".join("?" * len(artist_ids))
        db.execute(
            f"DELETE FROM tracks WHERE artist_id IN ({placeholders})",
            artist_ids,
        )
        db.execute(
            f"DELETE FROM albums WHERE artist_id IN ({placeholders})",
            artist_ids,
        )
        db.execute("DELETE FROM artists WHERE library_id=?", (library_id,))
    db.execute("DELETE FROM music_libraries WHERE id=?", (library_id,))
    current_id = get_current_library_id()
    if current_id == library_id:
        db.execute("DELETE FROM scan_meta WHERE key='current_library_id'")
    db.commit()


def get_scan_meta_value(key: str):
    db = get_db()
    row = db.execute("SELECT value FROM scan_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None
