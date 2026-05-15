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
            scanned_at  REAL
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
    """)
    db.commit()
    db.close()
