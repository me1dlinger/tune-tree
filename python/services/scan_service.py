"""
扫描服务
"""
import logging
import os
import time
import concurrent.futures
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

BATCH_SIZE = 500
MAX_WORKERS = 8  # 线程池大小，根据CPU核心数调整

def _load_existing_tracks() -> dict[str, dict]:
    db = get_db()
    rows = db.execute("SELECT path, id, mtime, size FROM tracks").fetchall()
    return {row["path"]: {"id": row["id"], "mtime": row["mtime"], "size": row["size"]} for row in rows}

def _batch_insert(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany("""
        INSERT INTO tracks
        (path,filename,ext,size,mtime,ctime,title,artist,album,album_artist,year,
         track_num,disc_num,duration,sample_rate,bitrate,has_cover,has_lyrics,
         pending,missing_tags,scanned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, tracks_data)

def _batch_update(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany("""
        UPDATE tracks SET filename=?,ext=?,size=?,mtime=?,ctime=?,title=?,artist=?,
        album=?,album_artist=?,year=?,track_num=?,disc_num=?,duration=?,
        sample_rate=?,bitrate=?,has_cover=?,has_lyrics=?,pending=?,
        missing_tags=?,scanned_at=?
        WHERE path=?
    """, tracks_data)

def _process_file(filepath, existing_tracks, scanned_at):
    """处理单个音频文件，返回（操作类型，数据，艺术家）"""
    path_str = str(filepath)
    filename = filepath.name
    
    try:
        stat = filepath.stat()
    except OSError:
        return None
    
    mtime = stat.st_mtime
    ctime = stat.st_ctime
    size = stat.st_size

    existing = existing_tracks.get(path_str)
    if existing and abs(existing["mtime"] - mtime) < 1 and existing["size"] == size:
        return ("skip", None, None)

    meta = read_metadata(path_str)
    missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
    pending = 1 if missing else 0
    missing_str = ",".join(missing) if missing else ""

    artist = meta.get("artist") or ""

    track_data = (
        path_str, filename, filepath.suffix.lower().lstrip("."),
        size, mtime, ctime,
        meta["title"], meta["artist"], meta["album"],
        meta["album_artist"], meta["year"],
        meta["track_num"], meta["disc_num"],
        meta["duration"], meta["sample_rate"], meta["bitrate"],
        meta["has_cover"], meta["has_lyrics"],
        pending, missing_str, scanned_at
    )

    if existing:
        return ("update", track_data[1:] + (path_str,), artist)
    else:
        return ("insert", track_data, artist)


def scan_library(root: str) -> dict:
    root_path = Path(root)
    found_paths: set[str] = set()
    existing_tracks = _load_existing_tracks()
    existing_paths = set(existing_tracks.keys())

    pending_inserts = []
    pending_updates = []
    added = updated = skipped = 0
    scanned_at = time.time()
    scan_start_time = time.time()  # 记录扫描开始时间
    
    # 收集变化的艺术家
    changed_artists = set()
    
    # 收集所有音频文件路径
    audio_files = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames.sort()
        for filename in filenames:
            if Path(filename).suffix.lower() in AUDIO_EXTS:
                audio_files.append(Path(dirpath) / filename)
    
    logger.info(f"开始扫描：共发现 {len(audio_files)} 个音频文件")
    
    # 使用线程池并行处理文件
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有任务，使用字典映射future到文件路径
        future_to_path = {}
        for filepath in audio_files:
            future = executor.submit(_process_file, filepath, existing_tracks, scanned_at)
            future_to_path[future] = str(filepath)
        
        # 处理结果
        for future in concurrent.futures.as_completed(future_to_path):
            try:
                result = future.result()
                if result is None:
                    continue
                    
                op_type, data, artist = result
                found_paths.add(future_to_path[future])
                
                if op_type == "skip":
                    skipped += 1
                elif op_type == "insert":
                    pending_inserts.append(data)
                    if artist:
                        changed_artists.add(artist)
                elif op_type == "update":
                    pending_updates.append(data)
                    if artist:
                        changed_artists.add(artist)
                    
                # 批量写入
                if len(pending_inserts) >= BATCH_SIZE:
                    db = get_db()
                    _batch_insert(db, pending_inserts)
                    added += len(pending_inserts)
                    pending_inserts = []

                if len(pending_updates) >= BATCH_SIZE:
                    db = get_db()
                    _batch_update(db, pending_updates)
                    updated += len(pending_updates)
                    pending_updates = []
                    
            except Exception as e:
                logger.error(f"处理文件时出错: {e}")

    # 处理剩余数据
    if pending_inserts:
        db = get_db()
        _batch_insert(db, pending_inserts)
        added += len(pending_inserts)

    if pending_updates:
        db = get_db()
        _batch_update(db, pending_updates)
        updated += len(pending_updates)

    stale_paths = existing_paths - found_paths
    if stale_paths:
        db = get_db()
        # 分批删除，避免SQL参数过多，同时收集被删除的艺术家
        chunk_size = 1000
        stale_list = list(stale_paths)
        for i in range(0, len(stale_list), chunk_size):
            chunk = stale_list[i:i+chunk_size]
            placeholders = ",".join("?" * len(chunk))
            # 查询被删除的艺术家
            artist_rows = db.execute(
                f"SELECT DISTINCT artist FROM tracks WHERE path IN ({placeholders})",
                tuple(chunk)
            ).fetchall()
            for row in artist_rows:
                if row["artist"]:
                    changed_artists.add(row["artist"])
            db.execute(f"DELETE FROM tracks WHERE path IN ({placeholders})", tuple(chunk))
        removed = len(stale_list)
    else:
        removed = 0

    scan_duration = time.time() - scan_start_time
    hours = int(scan_duration // 3600)
    minutes = int((scan_duration % 3600) // 60)
    seconds = int(scan_duration % 60)
    duration_str = ""
    if hours > 0:
        duration_str += f"{hours}小时"
    if minutes > 0 or hours > 0:
        duration_str += f"{minutes}分钟"
    duration_str += f"{seconds}秒"
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_scan_meta("last_scan", now)
    add_op_log(now, "scan", f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed} · 耗时 {duration_str}")
    logger.info(f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed} · 耗时 {duration_str}")
    commit()
    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "removed": removed,
        "changed_artists": list(changed_artists)
    }
