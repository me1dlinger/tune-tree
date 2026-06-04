"""
数据库模块
"""
import os
import sqlite3
from flask import g
from config import DB_PATH

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
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
        CREATE INDEX IF NOT EXISTS idx_artist ON tracks(artist);
        CREATE INDEX IF NOT EXISTS idx_album  ON tracks(album);
        CREATE TABLE IF NOT EXISTS scan_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS op_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            op_type   TEXT NOT NULL,
            message   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS track_cooldown (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id    INTEGER NOT NULL,
            cooldown_until REAL NOT NULL,
            reason      TEXT NOT NULL,
            created_at  REAL NOT NULL,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cooldown_track ON track_cooldown(track_id);
        CREATE INDEX IF NOT EXISTS idx_cooldown_until ON track_cooldown(cooldown_until);
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
    """)
    try:
        db.execute("ALTER TABLE tracks ADD COLUMN scrape_failed INTEGER DEFAULT 0;")
    except sqlite3.OperationalError:
        pass

    
    db.commit()
    db.close()
