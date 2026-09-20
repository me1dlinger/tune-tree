"""
扫描服务
"""

import logging
import os
import re
import time
import threading
import unicodedata
import concurrent.futures
from datetime import datetime
from pathlib import Path


def _normalize_path(path: str) -> str:
    """对路径进行Unicode正规化，处理日语假名等字符的不同表示形式"""
    return unicodedata.normalize("NFC", path)


from utils.metadata import read_metadata, normalize_str, extract_cover_to_file
from utils.formatting import safe_dirname
from models.db import get_db
from repository.track_repository import (
    get_all_track_paths,
    delete_track_by_path,
    set_scan_meta,
    set_scan_heartbeat,
    add_op_log,
    commit,
)
from repository.artist_repository import (
    ensure_artist,
    delete_artist,
)
from repository.album_repository import (
    ensure_album,
    update_album,
    delete_album,
)

AUDIO_EXTS = {".mp3", ".flac"}


def _format_duration(seconds: float) -> str:
    """把秒数格式化为可读的中文时长字符串"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}小时{minutes}分钟{secs}秒"
    if minutes > 0:
        return f"{minutes}分钟{secs}秒"
    return f"{secs}秒"
_ORGANIZED_FILENAME_RE = re.compile(r"^\d{2}\.\s+.+\.(?:mp3|flac)$", re.IGNORECASE)
logger = logging.getLogger("tunetree")

BATCH_SIZE = 1000
# 元数据读取是 IO 密集 + mutagen(C 库)解析，适当提高并行度可显著加快扫描。
# 上限 8 避免在低核机器上造成过度争抢。
MAX_WORKERS = min(os.cpu_count() or 4, 8)


def _is_organized_path(filepath: Path, music_root: str) -> bool:
    """判断文件路径是否匹配 organized 目录结构: music_root/artist_dir/album_dir/NN. Title.ext"""
    try:
        rel = filepath.relative_to(music_root)
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) != 3:
        return False
    filename = parts[2]
    return bool(_ORGANIZED_FILENAME_RE.match(filename))


def _ensure_artist_local(db, name: str, library_id: int | None = None) -> int:
    """Ensure artist exists WITHOUT committing — uses passed connection."""
    name_norm = normalize_str(name)
    if library_id is not None:
        row = db.execute(
            "SELECT id, library_id FROM artists WHERE name_normalized=? AND (library_id=? OR library_id IS NULL)",
            (name_norm, library_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT id, library_id FROM artists WHERE name_normalized=?" ,
            (name_norm,),
        ).fetchone()
    if row is not None:
        if library_id is not None and row["library_id"] is None:
            db.execute("UPDATE artists SET library_id=? WHERE id=?", (library_id, row["id"]))
        return row["id"]
    now = time.time()
    cursor = db.execute(
        "INSERT INTO artists (name, name_normalized, dir_name, cover_path, library_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (name, name_norm, safe_dirname(name), None, library_id, now, now),
    )
    return cursor.lastrowid


def _ensure_album_local(db, title: str, artist_id: int, year=None, library_id: int | None = None) -> int:
    """Ensure album exists WITHOUT committing — uses passed connection."""
    title_norm = normalize_str(title)
    row = db.execute(
        "SELECT id, year, library_id FROM albums WHERE title_normalized=? AND artist_id=?",
        (title_norm, artist_id),
    ).fetchone()
    if row is not None:
        needs_update = False
        if year and not row["year"]:
            db.execute("UPDATE albums SET year=? WHERE id=?", (year, row["id"]))
            needs_update = True
        if library_id is not None and row["library_id"] is None:
            db.execute("UPDATE albums SET library_id=? WHERE id=?", (library_id, row["id"]))
            needs_update = True
        return row["id"]
    now = time.time()
    cursor = db.execute(
        "INSERT INTO albums (title, title_normalized, artist_id, dir_name, year, library_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (title, title_norm, artist_id, safe_dirname(title), year, library_id, now, now),
    )
    return cursor.lastrowid


def _load_existing_tracks(library_id: int | None = None) -> dict[str, dict]:
    db = get_db()
    if library_id is not None:
        rows = db.execute(
            "SELECT path, id, mtime, size FROM tracks WHERE library_id=?",
            (library_id,),
        ).fetchall()
    else:
        rows = db.execute("SELECT path, id, mtime, size FROM tracks").fetchall()
    return {
        _normalize_path(row["path"]): {
            "id": row["id"],
            "mtime": row["mtime"],
            "size": row["size"],
            "original_path": row["path"],
        }
        for row in rows
    }


def _batch_insert(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany(
        """
        INSERT INTO tracks
        (path,filename,ext,size,mtime,ctime,title,artist,album,album_artist,year,
         track_num,disc_num,duration,sample_rate,bitrate,has_cover,has_lyrics,
         pending,missing_tags,scanned_at,organized,artist_id,album_id,track_artist,library_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
        tracks_data,
    )


def _batch_update(db, tracks_data: list):
    if not tracks_data:
        return
    db.executemany(
        """
        UPDATE tracks SET path=?,filename=?,ext=?,size=?,mtime=?,ctime=?,title=?,artist=?,
        album=?,album_artist=?,year=?,track_num=?,disc_num=?,duration=?,
        sample_rate=?,bitrate=?,has_cover=?,has_lyrics=?,pending=?,
        missing_tags=?,scanned_at=?,organized=?,artist_id=?,album_id=?,track_artist=?,library_id=?
        WHERE id=?
    """,
        tracks_data,
    )


def _process_file(filepath, existing_tracks, scanned_at, music_root):
    """处理单个音频文件，返回（操作类型，数据，元信息dict）"""
    path_str = str(filepath)
    path_normalized = _normalize_path(path_str)
    filename = filepath.name
    try:
        stat = filepath.stat()
    except OSError:
        return None

    mtime = stat.st_mtime
    ctime = stat.st_ctime
    size = stat.st_size
    existing = existing_tracks.get(path_normalized)
    if existing and int(existing["mtime"]) == int(mtime) and existing["size"] == size:
        return ("skip", None, None)

    meta = read_metadata(path_str)
    missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
    pending = 1 if missing else 0
    missing_str = ",".join(missing) if missing else ""

    artist_name = normalize_str(meta.get("artist") or "")
    album_name = meta.get("album") or ""
    album_artist_name = meta.get("album_artist") or ""
    track_artist_name = artist_name
    year = meta.get("year")

    organized = 1 if (not pending and _is_organized_path(filepath, music_root)) else 0

    track_data = (
        path_str,
        filename,
        filepath.suffix.lower().lstrip("."),
        size,
        mtime,
        ctime,
        meta["title"],
        normalize_str(meta["artist"]) if meta.get("artist") else None,
        meta["album"],
        meta["album_artist"],
        meta["year"],
        meta["track_num"],
        meta["disc_num"],
        meta["duration"],
        meta["sample_rate"],
        meta["bitrate"],
        meta["has_cover"],
        meta["has_lyrics"],
        pending,
        missing_str,
        scanned_at,
        organized,
    )
    meta_info = {
        "artist_name": artist_name,
        "album_name": album_name,
        "album_artist_name": album_artist_name,
        "track_artist_name": track_artist_name,
        "year": year,
    }

    if existing:
        return ("update", track_data, existing["id"], meta_info)
    else:
        return ("insert", track_data, meta_info)


def scan_library(
    root: str,
    library_id: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """扫描音乐库。

    cancel_event: 传入 threading.Event 后可协作式取消——扫描循环会定期检查，
    一旦被置位即停止并返回已处理的部分结果（cancelled=True）。
    """
    root_path = Path(root)
    found_paths: set[str] = set()
    existing_tracks = _load_existing_tracks(library_id)
    existing_paths = set(existing_tracks.keys())

    pending_inserts = []
    pending_updates = []
    added = updated = skipped = 0
    scanned_at = time.time()
    scan_start_time = time.time()
    cancelled = False

    changed_artists = set()

    artist_id_cache: dict[str, int] = {}
    album_id_cache: dict[tuple[int, str], int] = {}

    audio_files = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        dirnames[:] = [d for d in dirnames if d != ".upload_temp"]
        dirnames.sort()
        for filename in filenames:
            if Path(filename).suffix.lower() in AUDIO_EXTS:
                audio_files.append(Path(dirpath) / filename)

    logger.info(f"开始扫描：共发现 {len(audio_files)} 个音频文件")

    # 手动管理执行器：取消时可用 cancel_futures 尽快丢弃尚未运行的子任务
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
    future_to_path = {}

    # 批处理缓存：_collect_batch 会按批落库并把已落库的列表替换为空列表，
    # 因此后续读取必须统一走 pending（不能再用外层 pending_inserts 别名）
    pending = {"inserts": pending_inserts, "updates": pending_updates}

    def _collect_batch():
        """按批落库，避免频繁小事务"""
        nonlocal added, updated
        if len(pending["inserts"]) >= BATCH_SIZE:
            _batch_insert(get_db(), pending["inserts"])
            added += len(pending["inserts"])
            pending["inserts"] = []
        if len(pending["updates"]) >= BATCH_SIZE:
            _batch_update(get_db(), pending["updates"])
            updated += len(pending["updates"])
            pending["updates"] = []

    try:
        for filepath in audio_files:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            logger.debug(f"处理文件: {filepath}")
            future = executor.submit(
                _process_file, filepath, existing_tracks, scanned_at, root
            )
            future_to_path[future] = _normalize_path(str(filepath))

        processed = 0
        for future in concurrent.futures.as_completed(future_to_path):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            processed += 1
            if processed % 20 == 0:
                # 定期心跳：让 /api/scan/status 能判断扫描线程是否仍在推进
                try:
                    set_scan_heartbeat()
                except Exception:  # noqa: BLE001
                    pass
            try:
                result = future.result()
                if result is None:
                    continue

                if result[0] == "update":
                    op_type, data, existing_id, meta_info = result
                elif result[0] == "skip":
                    found_paths.add(future_to_path[future])
                    skipped += 1
                    continue
                else:
                    op_type, data, meta_info = result
                    existing_id = None
                found_paths.add(future_to_path[future])

                if op_type == "skip":
                    skipped += 1
                    continue

                artist_name = meta_info["artist_name"] if meta_info else ""
                album_name = meta_info["album_name"] if meta_info else ""
                album_artist_name = meta_info["album_artist_name"] if meta_info else ""
                track_artist_name = meta_info["track_artist_name"] if meta_info else ""
                year = meta_info["year"] if meta_info else None

                artist_id = None
                album_id = None

                effective_artist = album_artist_name or artist_name
                if effective_artist:
                    if effective_artist not in artist_id_cache:
                        artist_id_cache[effective_artist] = _ensure_artist_local(
                            get_db(), effective_artist, library_id=library_id
                        )
                    artist_id = artist_id_cache[effective_artist]

                    if album_name:
                        cache_key = (artist_id, normalize_str(album_name))
                        if cache_key not in album_id_cache:
                            album_id_cache[cache_key] = _ensure_album_local(
                                get_db(),
                                album_name,
                                artist_id,
                                year=year,
                                library_id=library_id,
                            )
                        album_id = album_id_cache[cache_key]

                extended_data = data + (
                    artist_id,
                    album_id,
                    track_artist_name,
                    library_id,
                )

                if op_type == "insert":
                    pending["inserts"].append(extended_data)
                    if artist_name:
                        changed_artists.add(artist_name)
                elif op_type == "update":
                    pending["updates"].append(extended_data + (existing_id,))
                    if artist_name:
                        changed_artists.add(artist_name)

                _collect_batch()

            except Exception as e:
                logger.error(f"处理文件时出错: {e}")
    finally:
        # 取消时尽快丢弃尚未启动的子任务，不等待它们
        executor.shutdown(wait=False, cancel_futures=True)
        # 注意：executor 线程若仍在解析大文件，会在进程结束前自然退出，
        # 这里选择 wait=False 以便取消请求能尽快返回。

    # 收尾落库：_collect_batch 会把快满的批落库并列重置为空，这里 flush 剩余记录
    if pending["inserts"]:
        _batch_insert(get_db(), pending["inserts"])
        added += len(pending["inserts"])

    if pending["updates"]:
        _batch_update(get_db(), pending["updates"])
        updated += len(pending["updates"])

    if cancelled:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        add_op_log(
            now,
            "scan",
            f"扫描被用户取消（已处理 {added + updated + skipped} 个文件：新增 {added} 更新 {updated} 跳过 {skipped}）",
            library_id=library_id,
        )
        logger.info("扫描被用户取消：新增 %d 更新 %d 跳过 %d", added, updated, skipped)
        commit()
        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "removed": 0,
            "duration": _format_duration(time.time() - scan_start_time),
            "cancelled": True,
            "changed_artists": list(changed_artists),
        }

    stale_paths = existing_paths - found_paths
    if stale_paths:
        db = get_db()
        stale_list = list(stale_paths)
        placeholders = ",".join("?" * len(stale_list))
        artist_rows = db.execute(
            f"SELECT DISTINCT artist FROM tracks WHERE path IN ({placeholders})",
            tuple(stale_list),
        ).fetchall()
        for row in artist_rows:
            if row["artist"]:
                changed_artists.add(row["artist"])
        db.execute(
            f"DELETE FROM tracks WHERE path IN ({placeholders})", tuple(stale_list)
        )
        removed = len(stale_list)
    else:
        removed = 0

    _backfill_artist_album_ids(library_id)
    _ensure_covers(root)
    _cleanup_orphaned_artists_albums()

    scan_duration = time.time() - scan_start_time
    duration_str = _format_duration(scan_duration)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_scan_meta("last_scan", now)
    add_op_log(
        now,
        "scan",
        f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed} · 耗时 {duration_str}",
        library_id=library_id,
    )
    logger.info(
        f"扫描完成：新增 {added} 更新 {updated} 跳过 {skipped} 移除 {removed} · 耗时 {duration_str}"
    )
    commit()

    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "removed": removed,
        "duration": duration_str,
        "changed_artists": list(changed_artists),
    }


def _backfill_artist_album_ids(library_id: int | None = None):
    """为已有但缺少 artist_id/album_id 的 tracks 回填关联ID"""
    db = get_db()
    if library_id:
        rows = db.execute(
            "SELECT id, artist, album, album_artist FROM tracks WHERE (artist_id IS NULL OR library_id IS NULL) AND artist IS NOT NULL AND artist != '' AND library_id=?",
            (library_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, artist, album, album_artist FROM tracks WHERE (artist_id IS NULL OR library_id IS NULL) AND artist IS NOT NULL AND artist != ''"
        ).fetchall()

    if not rows:
        return

    artist_cache: dict[str, int] = {}
    album_cache: dict[tuple[int, str], int] = {}
    backfilled = 0

    for row in rows:
        artist_name = row["artist"]
        album_name = row["album"] or ""
        album_artist_name = row["album_artist"] or ""

        effective_artist = album_artist_name or artist_name
        if effective_artist not in artist_cache:
            artist_cache[effective_artist] = _ensure_artist_local(
                db, effective_artist, library_id=library_id
            )
        artist_id = artist_cache[effective_artist]

        album_id = None
        if album_name:
            cache_key = (artist_id, normalize_str(album_name))
            if cache_key not in album_cache:
                album_cache[cache_key] = _ensure_album_local(
                    db, album_name, artist_id, library_id=library_id
                )
            album_id = album_cache[cache_key]

        db.execute(
            "UPDATE tracks SET artist_id=?, album_id=?, library_id=? WHERE id=?",
            (artist_id, album_id, library_id, row["id"]),
        )
        backfilled += 1

    if backfilled > 0:
        logger.info(f"回填完成：{backfilled} 条 track 的 artist_id/album_id")


ALBUM_COVER_FILENAME = "cover.jpg"
ARTIST_COVER_FILENAME = "cover.jpg"


def _ensure_covers(music_root: str):
    db = get_db()

    artists = db.execute("SELECT id, dir_name, cover_path FROM artists").fetchall()
    artist_cover_updated = 0
    for artist in artists:
        artist_dir = os.path.join(music_root, artist["dir_name"])
        cover_path = os.path.join(artist_dir, ARTIST_COVER_FILENAME)
        if os.path.exists(cover_path) and not artist["cover_path"]:
            db.execute("UPDATE artists SET cover_path=? WHERE id=?", (cover_path, artist["id"]))
            artist_cover_updated += 1
    if artist_cover_updated > 0:
        logger.info(f"艺术家封面更新完成：{artist_cover_updated} 个艺术家")

    albums = db.execute(
        "SELECT id, artist_id, dir_name, cover_path FROM albums"
    ).fetchall()
    if not albums:
        return

    artist_cache: dict[int, str | None] = {}
    extracted = 0

    for album in albums:
        album_id = album["id"]
        artist_id = album["artist_id"]
        album_dir_name = album["dir_name"]

        if artist_id not in artist_cache:
            row = db.execute(
                "SELECT dir_name FROM artists WHERE id=?", (artist_id,)
            ).fetchone()
            artist_cache[artist_id] = row["dir_name"] if row else None
        artist_dir_name = artist_cache[artist_id]
        if not artist_dir_name:
            continue

        album_dir = os.path.join(music_root, artist_dir_name, album_dir_name)
        cover_path = os.path.join(album_dir, ALBUM_COVER_FILENAME)

        if os.path.exists(cover_path):
            if not album["cover_path"]:
                db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album_id))
            continue

        first_track = db.execute(
            "SELECT path FROM tracks WHERE album_id=? AND has_cover=1 ORDER BY disc_num, track_num LIMIT 1",
            (album_id,),
        ).fetchone()
        if not first_track:
            continue

        if extract_cover_to_file(first_track["path"], cover_path):
            db.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_path, album_id))
            extracted += 1

    if extracted > 0:
        logger.info(f"专辑封面提取完成：{extracted} 个专辑")


def _cleanup_orphaned_artists_albums():
    """清理不再被任何 track 引用的 artists 和 albums — 单条SQL批量操作，避免逐行提交"""
    db = get_db()

    db.execute("""
        DELETE FROM albums WHERE NOT EXISTS (
            SELECT 1 FROM tracks WHERE tracks.album_id = albums.id
        )
    """)
    deleted_albums = db.execute("SELECT changes()").fetchone()[0]

    db.execute("""
        DELETE FROM artists WHERE NOT EXISTS (
            SELECT 1 FROM tracks WHERE tracks.artist_id = artists.id
        )
    """)
    deleted_artists = db.execute("SELECT changes()").fetchone()[0]

    if deleted_albums > 0 or deleted_artists > 0:
        logger.info(
            f"清理孤立记录：{deleted_albums} 个专辑，{deleted_artists} 个艺术家"
        )
