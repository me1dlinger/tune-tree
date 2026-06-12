"""
扫描服务
"""

import logging
import os
import time
import unicodedata
import concurrent.futures
from datetime import datetime
from pathlib import Path


def _normalize_path(path: str) -> str:
    """对路径进行Unicode正规化，处理日语假名等字符的不同表示形式"""
    return unicodedata.normalize("NFC", path)


from utils.metadata import read_metadata, normalize_str, extract_cover_to_file
from models.db import get_db
from repository.track_repository import (
    get_all_track_paths,
    delete_track_by_path,
    set_scan_meta,
    add_op_log,
    commit,
)
from repository.artist_repository import ensure_artist, delete_artist
from repository.album_repository import ensure_album, update_album, delete_album

AUDIO_EXTS = {".mp3", ".flac"}
logger = logging.getLogger("tunetree")

BATCH_SIZE = 500
MAX_WORKERS = 8  # 线程池大小，根据CPU核心数调整


def _load_existing_tracks(library_id: int | None = None) -> dict[str, dict]:
    db = get_db()
    if library_id is not None:
        rows = db.execute(
            "SELECT t.path, t.id, t.mtime, t.size FROM tracks t JOIN artists a ON t.artist_id = a.id WHERE a.library_id=?",
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
         pending,missing_tags,scanned_at,artist_id,album_id,track_artist)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        missing_tags=?,scanned_at=?,artist_id=?,album_id=?,track_artist=?
        WHERE id=?
    """,
        tracks_data,
    )


def _process_file(filepath, existing_tracks, scanned_at):
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


def scan_library(root: str, library_id: int | None = None) -> dict:
    root_path = Path(root)
    found_paths: set[str] = set()
    existing_tracks = _load_existing_tracks(library_id)
    existing_paths = set(existing_tracks.keys())

    pending_inserts = []
    pending_updates = []
    added = updated = skipped = 0
    scanned_at = time.time()
    scan_start_time = time.time()

    changed_artists = set()

    artist_id_cache: dict[str, int] = {}
    album_id_cache: dict[tuple[int, str], int] = {}

    audio_files = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d != ".upload_temp"]
        dirnames.sort()
        for filename in filenames:
            if Path(filename).suffix.lower() in AUDIO_EXTS:
                audio_files.append(Path(dirpath) / filename)

    logger.info(f"开始扫描：共发现 {len(audio_files)} 个音频文件")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_path = {}
        for filepath in audio_files:
            logger.debug(f"处理文件: {filepath}")
            future = executor.submit(
                _process_file, filepath, existing_tracks, scanned_at
            )
            future_to_path[future] = _normalize_path(str(filepath))

        for future in concurrent.futures.as_completed(future_to_path):
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
                        artist_id_cache[effective_artist] = ensure_artist(
                            effective_artist, library_id=library_id
                        )
                    artist_id = artist_id_cache[effective_artist]

                    if album_name:
                        cache_key = (artist_id, normalize_str(album_name))
                        if cache_key not in album_id_cache:
                            album_id_cache[cache_key] = ensure_album(
                                album_name, artist_id, year=year
                            )
                        album_id = album_id_cache[cache_key]

                extended_data = data + (artist_id, album_id, track_artist_name)

                if op_type == "insert":
                    pending_inserts.append(extended_data)
                    if artist_name:
                        changed_artists.add(artist_name)
                elif op_type == "update":
                    pending_updates.append(extended_data + (existing_id,))
                    if artist_name:
                        changed_artists.add(artist_name)

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
        chunk_size = 1000
        stale_list = list(stale_paths)
        for i in range(0, len(stale_list), chunk_size):
            chunk = stale_list[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            artist_rows = db.execute(
                f"SELECT DISTINCT artist FROM tracks WHERE path IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for row in artist_rows:
                if row["artist"]:
                    changed_artists.add(row["artist"])
            db.execute(
                f"DELETE FROM tracks WHERE path IN ({placeholders})", tuple(chunk)
            )
        removed = len(stale_list)
    else:
        removed = 0

    _backfill_artist_album_ids(library_id)
    _ensure_covers(root)
    _cleanup_orphaned_artists_albums()

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
            "SELECT id, artist, album, album_artist FROM tracks WHERE artist_id IS NULL AND artist IS NOT NULL AND artist != '' AND artist_id IN (SELECT id FROM artists WHERE library_id=?)",
            (library_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, artist, album, album_artist FROM tracks WHERE artist_id IS NULL AND artist IS NOT NULL AND artist != ''"
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
            artist_cache[effective_artist] = ensure_artist(
                effective_artist, library_id=library_id
            )
        artist_id = artist_cache[effective_artist]

        album_id = None
        if album_name:
            cache_key = (artist_id, normalize_str(album_name))
            if cache_key not in album_cache:
                album_cache[cache_key] = ensure_album(album_name, artist_id)
            album_id = album_cache[cache_key]

        db.execute(
            "UPDATE tracks SET artist_id=?, album_id=? WHERE id=?",
            (artist_id, album_id, row["id"]),
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
        if os.path.exists(cover_path):
            if not artist["cover_path"]:
                from repository.artist_repository import update_artist

                update_artist(artist["id"], cover_path=cover_path)
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
                update_album(album_id, cover_path=cover_path)
            continue

        first_track = db.execute(
            "SELECT path FROM tracks WHERE album_id=? AND has_cover=1 ORDER BY disc_num, track_num LIMIT 1",
            (album_id,),
        ).fetchone()
        if not first_track:
            continue

        if extract_cover_to_file(first_track["path"], cover_path):
            update_album(album_id, cover_path=cover_path)
            extracted += 1

    if extracted > 0:
        logger.info(f"专辑封面提取完成：{extracted} 个专辑")


def _cleanup_orphaned_artists_albums():
    """清理不再被任何 track 引用的 artists 和 albums"""
    db = get_db()

    orphan_albums = db.execute("""
        SELECT al.id FROM albums al
        WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id)
    """).fetchall()
    for row in orphan_albums:
        delete_album(row["id"])

    orphan_artists = db.execute("""
        SELECT a.id FROM artists a
        WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.artist_id = a.id)
    """).fetchall()
    for row in orphan_artists:
        delete_artist(row["id"])

    cleaned = len(orphan_albums) + len(orphan_artists)
    if cleaned > 0:
        logger.info(
            f"清理孤立记录：{len(orphan_albums)} 个专辑，{len(orphan_artists)} 个艺术家"
        )
