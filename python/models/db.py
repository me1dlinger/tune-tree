"""
数据库模块
"""

import os
import sqlite3
import logging
import threading
from flask import g
from config import DB_PATH

logger = logging.getLogger("tunetree")

_local = threading.local()


def _apply_pragmas(db: sqlite3.Connection):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-64000")
    db.execute("PRAGMA busy_timeout=5000")


def _create_db_connection() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    db.row_factory = sqlite3.Row
    _apply_pragmas(db)
    return db


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _create_db_connection()
    return g.db


def get_db_background() -> sqlite3.Connection:
    """Get DB connection for background threads (scheduler, scan workers).

    Uses thread-local storage instead of Flask ``g`` so it works outside
    request / app contexts.
    """
    if not hasattr(_local, "db") or _local.db is None:
        _local.db = _create_db_connection()
    return _local.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def close_db_background():
    """Close the background-thread connection, if one exists."""
    db = getattr(_local, "db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
        _local.db = None


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS artists (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            name_normalized TEXT NOT NULL,
            dir_name        TEXT NOT NULL,
            cover_path      TEXT,
            library_id      INTEGER,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS albums (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            title             TEXT NOT NULL,
            title_normalized  TEXT NOT NULL,
            artist_id         INTEGER NOT NULL,
            dir_name          TEXT NOT NULL,
            cover_path        TEXT,
            year              TEXT,
            library_id      INTEGER,
            created_at        REAL NOT NULL,
            updated_at        REAL NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS tracks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            path        TEXT UNIQUE NOT NULL,
            filename    TEXT NOT NULL,
            ext         TEXT NOT NULL,
            size        INTEGER,
            mtime       REAL,
            ctime       REAL,
            title       TEXT,
            artist      TEXT,
            album       TEXT,
            album_artist TEXT,
            artist_id   INTEGER,
            album_id    INTEGER,
            library_id      INTEGER,
            track_artist TEXT,
            year        TEXT,
            track_num   INTEGER,
            disc_num    INTEGER,
            duration    REAL,
            sample_rate INTEGER,
            bitrate     INTEGER,
            has_cover   INTEGER DEFAULT 0,
            has_lyrics  INTEGER DEFAULT 0,
            organized   INTEGER DEFAULT 0,
            pending     INTEGER DEFAULT 0,
            missing_tags TEXT,
            scanned_at  REAL,
            scrape_failed INTEGER DEFAULT 0

        );

        CREATE TABLE IF NOT EXISTS scan_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS op_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            op_type   TEXT NOT NULL,
            message   TEXT,
            library_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS track_cooldown (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id    INTEGER NOT NULL,
            cooldown_until REAL NOT NULL,
            reason      TEXT NOT NULL,
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_config (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_enabled  INTEGER DEFAULT 0,
            organize_enabled INTEGER DEFAULT 0,
            interval_minutes INTEGER DEFAULT 60,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_status (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type       TEXT NOT NULL,
            status          TEXT NOT NULL,
            last_run_at     REAL,
            last_success_at REAL,
            last_failure_at REAL,
            next_run_at     REAL,
            error_message   TEXT,
            run_count       INTEGER DEFAULT 0,
            success_count   INTEGER DEFAULT 0,
            failure_count   INTEGER DEFAULT 0,
            is_manual       INTEGER DEFAULT 0,
            updated_at      REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS music_libraries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            path        TEXT NOT NULL,
            is_default  INTEGER DEFAULT 0,
            needs_config INTEGER DEFAULT 0,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        );
    """)
    for alter_sql in [
        "ALTER TABLE tracks ADD COLUMN scrape_failed INTEGER DEFAULT 0;",
        "ALTER TABLE tracks ADD COLUMN artist_id INTEGER;",
        "ALTER TABLE tracks ADD COLUMN album_id INTEGER;",
        "ALTER TABLE tracks ADD COLUMN track_artist TEXT;",
        "ALTER TABLE tracks ADD COLUMN library_id INTEGER;",
        "ALTER TABLE artists ADD COLUMN library_id INTEGER;",
        "ALTER TABLE albums ADD COLUMN library_id INTEGER;",
        "ALTER TABLE op_log ADD COLUMN library_id INTEGER;",
    ]:
        try:
            db.execute(alter_sql)
        except sqlite3.OperationalError:
            pass

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_artist ON tracks(artist);",
        "CREATE INDEX IF NOT EXISTS idx_artists_library_id ON artists(library_id);",
        "CREATE INDEX IF NOT EXISTS idx_artists_dir_name_lib ON artists(dir_name, library_id);",
        "CREATE INDEX IF NOT EXISTS idx_artists_name_norm_lib ON artists(name_normalized, library_id);",
        "CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id);",
        "CREATE INDEX IF NOT EXISTS idx_albums_library_id ON albums(library_id);",
        "CREATE INDEX IF NOT EXISTS idx_album  ON tracks(album);",
        "CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);",
        "CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);",
        "CREATE INDEX IF NOT EXISTS idx_tracks_library_id ON tracks(library_id);",
        "CREATE INDEX IF NOT EXISTS idx_cooldown_track ON track_cooldown(track_id);",
        "CREATE INDEX IF NOT EXISTS idx_cooldown_until ON track_cooldown(cooldown_until);",
        "CREATE INDEX IF NOT EXISTS idx_log_library_id ON op_log(library_id);",
    ]:
        try:
            db.execute(idx_sql)
        except sqlite3.OperationalError:
            pass

    _migrate_music_libraries(db)
    _migrate_library_id_to_tracks_albums(db)

    db.commit()
    db.close()


def _migrate_music_libraries(db):
    import time

    has_libraries = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='music_libraries'"
    ).fetchone()
    if not has_libraries:
        return

    existing_libs = db.execute("SELECT COUNT(*) FROM music_libraries").fetchone()[0]
    if existing_libs > 0:
        return

    now = time.time()
    artist_count = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    music_root = None
    music_root = os.environ.get("MUSIC_ROOT", None)
    logger.info(f"当前音乐库路径: {music_root}")
    if music_root and os.path.isdir(music_root):
        cursor = db.execute(
            "INSERT INTO music_libraries (name, path, is_default, needs_config, created_at, updated_at) VALUES (?, ?, 1, 0, ?, ?)",
            ("默认音乐库", music_root, now, now),
        )
        default_lib_id = cursor.lastrowid
        db.execute(
            "UPDATE artists SET library_id=? WHERE library_id IS NULL",
            (default_lib_id,),
        )
        logger.info(f"创建默认音乐库: {music_root} (id={default_lib_id})")
    elif artist_count > 0:
        cursor = db.execute(
            "INSERT INTO music_libraries (name, path, is_default, needs_config, created_at, updated_at) VALUES (?, ?, 1, 1, ?, ?)",
            ("待配置音乐库", "", now, now),
        )
        default_lib_id = cursor.lastrowid
        db.execute(
            "UPDATE artists SET library_id=? WHERE library_id IS NULL",
            (default_lib_id,),
        )
        logger.info(f"创建待配置音乐库 (id={default_lib_id})，需用户手动配置路径")


def _migrate_library_id_to_tracks_albums(db):
    has_col = db.execute(
        "SELECT COUNT(*) FROM pragma_table_info('tracks') WHERE name='library_id'"
    ).fetchone()[0]
    if not has_col:
        return

    unbackfilled_tracks = db.execute(
        "SELECT COUNT(*) FROM tracks WHERE library_id IS NULL"
    ).fetchone()[0]
    if unbackfilled_tracks > 0:
        db.execute(
            "UPDATE tracks SET library_id=(SELECT a.library_id FROM artists a WHERE a.id=tracks.artist_id) WHERE library_id IS NULL AND artist_id IS NOT NULL"
        )
        logger.info(f"回填 tracks.library_id: {unbackfilled_tracks} 条记录")

    unbackfilled_albums = db.execute(
        "SELECT COUNT(*) FROM albums WHERE library_id IS NULL"
    ).fetchone()[0]
    if unbackfilled_albums > 0:
        db.execute(
            "UPDATE albums SET library_id=(SELECT a.library_id FROM artists a WHERE a.id=albums.artist_id) WHERE library_id IS NULL AND artist_id IS NOT NULL"
        )
        logger.info(f"回填 albums.library_id: {unbackfilled_albums} 条记录")
