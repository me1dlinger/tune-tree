"""
扫描服务
"""
import logging
import time
from datetime import datetime
from pathlib import Path
from utils.metadata import read_metadata
from repository.track_repository import (
    get_track_id_and_mtime_by_path,
    get_all_track_paths,
    insert_track,
    update_track_by_path,
    delete_track_by_path,
    set_scan_meta,
    add_op_log,
    commit
)

AUDIO_EXTS = {".mp3", ".flac"}
logger = logging.getLogger("tunetree")

def scan_library(root: str) -> dict:
    root_path = Path(root)
    found_paths: set[str] = set()
    added = updated = skipped = 0

    for filepath in root_path.rglob("*"):
        if filepath.suffix.lower() not in AUDIO_EXTS:
            continue
        path_str = str(filepath)
        found_paths.add(path_str)
        stat = filepath.stat()
        mtime = stat.st_mtime

        row = get_track_id_and_mtime_by_path(path_str)
        if row and abs(row["mtime"] - mtime) < 1:
            skipped += 1
            continue

        meta = read_metadata(path_str)
        missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
        pending = 1 if missing else 0

        if row:
            update_track_by_path(
                filepath.name, filepath.suffix.lower(),
                stat.st_size, mtime,
                meta["title"], meta["artist"], meta["album"],
                meta["album_artist"], meta["year"],
                meta["track_num"], meta["disc_num"],
                meta["duration"], meta["sample_rate"], meta["bitrate"],
                meta["has_cover"], meta["has_lyrics"],
                pending, ",".join(missing) if missing else "",
                time.time(),
                path_str
            )
            updated += 1
        else:
            insert_track(
                path_str, filepath.name, filepath.suffix.lower(),
                stat.st_size, mtime,
                meta["title"], meta["artist"], meta["album"],
                meta["album_artist"], meta["year"],
                meta["track_num"], meta["disc_num"],
                meta["duration"], meta["sample_rate"], meta["bitrate"],
                meta["has_cover"], meta["has_lyrics"],
                pending, ",".join(missing) if missing else "",
                time.time()
            )
            added += 1

    # remove stale entries
    removed = 0
    for row in get_all_track_paths():
        if row["path"] not in found_paths:
            delete_track_by_path(row["path"])
            removed += 1

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_scan_meta("last_scan", now)
    commit()

    msg = f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed} — 共 {added+updated+skipped} 个文件"
    add_op_log(now, "scan", msg)
    commit()
    logger.info(msg)
    return {"added": added, "updated": updated, "skipped": skipped, "removed": removed}
