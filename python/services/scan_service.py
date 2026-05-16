"""
扫描服务
"""
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from utils.metadata import read_metadata
from models.db import get_db
from repository.track_repository import (
    get_all_track_paths,
    delete_track_by_path,
    set_scan_meta,
    add_op_log,
    commit
)

AUDIO_EXTS = {".mp3", ".flac"}
logger = logging.getLogger("tunetree")

BATCH_SIZE = 100

def _load_existing_tracks() -> dict[str, dict]:
    db = get_db()
    rows = db.execute("SELECT path, id, mtime, size FROM tracks").fetchall()
    return {row["path"]: {"id": row["id"], "mtime": row["mtime"], "size": row["size"]} for row in rows}

def _batch_insert(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany("""
        INSERT INTO tracks
        (path,filename,ext,size,mtime,title,artist,album,album_artist,year,
         track_num,disc_num,duration,sample_rate,bitrate,has_cover,has_lyrics,
         pending,missing_tags,scanned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, tracks_data)

def _batch_update(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany("""
        UPDATE tracks SET filename=?,ext=?,size=?,mtime=?,title=?,artist=?,
        album=?,album_artist=?,year=?,track_num=?,disc_num=?,duration=?,
        sample_rate=?,bitrate=?,has_cover=?,has_lyrics=?,pending=?,
        missing_tags=?,scanned_at=?
        WHERE path=?
    """, tracks_data)

def scan_library(root: str) -> dict:
    root_path = Path(root)
    found_paths: set[str] = set()
    existing_tracks = _load_existing_tracks()
    existing_paths = set(existing_tracks.keys())

    pending_inserts = []
    pending_updates = []
    added = updated = skipped = 0
    scanned_at = time.time()

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames.sort()
        audio_files = [f for f in filenames if Path(f).suffix.lower() in AUDIO_EXTS]
        for filename in audio_files:
            filepath = Path(dirpath) / filename
            path_str = str(filepath)
            found_paths.add(path_str)

            try:
                stat = filepath.stat()
            except OSError:
                continue
            mtime = stat.st_mtime
            size = stat.st_size

            existing = existing_tracks.get(path_str)
            if existing and abs(existing["mtime"] - mtime) < 1 and existing["size"] == size:
                skipped += 1
                continue

            meta = read_metadata(path_str)
            missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
            pending = 1 if missing else 0
            missing_str = ",".join(missing) if missing else ""

            track_data = (
                path_str, filename, filepath.suffix.lower().lstrip("."),
                size, mtime,
                meta["title"], meta["artist"], meta["album"],
                meta["album_artist"], meta["year"],
                meta["track_num"], meta["disc_num"],
                meta["duration"], meta["sample_rate"], meta["bitrate"],
                meta["has_cover"], meta["has_lyrics"],
                pending, missing_str, scanned_at
            )

            if existing:
                update_data = track_data + (path_str,)
                pending_updates.append(update_data)
            else:
                pending_inserts.append(track_data)

            if len(pending_inserts) >= BATCH_SIZE:
                db = get_db()
                _batch_insert(db, pending_inserts)
                pending_inserts = []

            if len(pending_updates) >= BATCH_SIZE:
                db = get_db()
                _batch_update(db, pending_updates)
                pending_updates = []

    if pending_inserts:
        db = get_db()
        _batch_insert(db, pending_inserts)
        added = len(pending_inserts)

    if pending_updates:
        db = get_db()
        _batch_update(db, pending_updates)
        updated = len(pending_updates)

    stale_paths = existing_paths - found_paths
    if stale_paths:
        db = get_db()
        placeholders = ",".join("?" * len(stale_paths))
        db.execute(f"DELETE FROM tracks WHERE path IN ({placeholders})", tuple(stale_paths))
        removed = len(stale_paths)
    else:
        removed = 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_scan_meta("last_scan", now)
    add_op_log(now, "scan", f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed}")
    logger.info(f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed}")
    commit()
    return {"added": added, "updated": updated, "skipped": skipped, "removed": removed}
