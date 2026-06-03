"""
格式化服务
"""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from config import MUSIC_ROOT
from utils.formatting import safe_dirname
from repository.track_repository import (
    get_tracks_by_ids,
    get_tracks_by_artist_and_album_id,
    get_artist_by_track_id,
    update_track_path_and_name,
    count_tracks_by_artist_with_status,
    add_op_log,
    commit,
)
from models.db import get_db

logger = logging.getLogger("tunetree")


def build_target_filename(track) -> str:
    """Build  `TrackNum. Title[ - feat. X].ext`"""
    num = track["track_num"] or 0
    title = (track["title"] or track["filename"]).strip()
    ext = track["ext"]
    if not ext.startswith("."):
        ext = "." + ext
    # 移除文件名中不允许的字符，特别是 / 会被识别为目录分隔符
    title = re.sub(r'[\\/:*?"<>|]', "_", title)
    title = re.sub(r"\s+", " ", title).strip()
    # extract feat from title if present
    feat_match = re.search(r"\(feat\.?\s*([^)]+)\)", title, re.IGNORECASE)
    if feat_match:
        feat = feat_match.group(1).strip()
        title = title[: feat_match.start()].strip()
        return f"{num:02d}. {title} - feat. {feat}{ext}"
    return f"{num:02d}. {title}{ext}"


def preview_format(
    artist: str, album_ids: list[int] | None = None, track_ids: list[int] | None = None
) -> dict:
    previews = []
    conflict_count = 0
    skip_count = 0
    seen_artists: dict[str, str] = {}
    seen_albums: dict[str, str] = {}
    seen_files: dict[str, str] = {}
    tree_structure = {}

    if track_ids and len(track_ids) > 0:
        rows = get_tracks_by_ids(track_ids)
    else:
        from repository.track_repository import get_albums_by_artist

        album_ids = album_ids or []
        albums_to_process = []

        if len(album_ids) > 0:
            for alb_id in album_ids:
                rows = get_tracks_by_artist_and_album_id(artist, alb_id)
                if rows:
                    albums_to_process.append((alb_id, rows[0]["album"], rows))
        else:
            albums = get_albums_by_artist(artist)
            for album in albums:
                alb_id = album["sample_id"]
                rows = get_tracks_by_artist_and_album_id(artist, alb_id)
                album_name = rows[0]["album"] if rows else album["album"]
                albums_to_process.append((alb_id, album_name, rows))

        all_rows = []
        for alb_id, album_name, rows in albums_to_process:
            if rows:
                all_rows.extend(rows)
        rows = all_rows

    # 预先获取所有目标路径，检查是否已存在于数据库中
    db = get_db()
    all_paths = [row["path"] for row in rows]
    existing_paths = {}
    if all_paths:
        placeholders = ",".join("?" * len(all_paths))
        existing_rows = db.execute(
            f"SELECT id, path FROM tracks WHERE path IN ({placeholders})",
            all_paths
        ).fetchall()
        for existing_row in existing_rows:
            existing_paths[existing_row["path"]] = existing_row["id"]

    for row in rows:
        if row["pending"]:
            continue
        artist_name = row["artist"] or "Unknown"
        album_name = row["album"] or "Unknown"
        artist_dir = safe_dirname(artist_name)
        album_dir = safe_dirname(album_name)

        artist_key = artist_dir.lower()

        if artist_key in seen_artists:
            actual_artist_dir = seen_artists[artist_key]
        else:
            actual_artist_dir = artist_dir
            seen_artists[artist_key] = artist_dir

        album_key = f"{artist_key}::{album_dir.lower()}"

        if album_key in seen_albums:
            actual_album_dir = seen_albums[album_key]
        else:
            actual_album_dir = album_dir
            seen_albums[album_key] = album_dir

        target_base = str(Path(MUSIC_ROOT) / actual_artist_dir / actual_album_dir)

        new_name = build_target_filename(row)
        stem = Path(new_name).stem
        ext = Path(new_name).suffix
        file_key = f"{target_base.lower()}::{stem.lower()}::{ext.lower()}"

        # 使用 Path 对象进行路径比较，避免字符串拼接带来的编码或分隔符问题
        target_path = str(Path(target_base) / new_name)
        # 对路径进行 Unicode 规范化，处理日文等非ASCII字符的规范化差异
        if target_path == row["path"]:
            status = "skip"
            skip_count += 1
        elif file_key in seen_files:
            status = "skip"
            skip_count += 1
        elif target_path in existing_paths and existing_paths[target_path] != row["id"]:
            # 目标路径已被其他track占用，标记为冲突
            status = "conflict"
            conflict_count += 1
        else:
            status = "normal"
            seen_files[file_key] = new_name

        if artist_name not in tree_structure:
            tree_structure[artist_name] = {}
        if album_name not in tree_structure[artist_name]:
            tree_structure[artist_name][album_name] = {
                "dir": f"{actual_artist_dir}/{actual_album_dir}",
                "tracks": [],
            }
        tree_structure[artist_name][album_name]["tracks"].append(
            {
                "filename": new_name,
                "track_num": row["track_num"],
            }
        )

        previews.append(
            {
                "old_path": row["path"],
                "old_name": row["filename"],
                "new_path": target_path,
                "new_name": new_name,
                "status": status,
                "track_id": row["id"],
            }
        )

    return {
        "items": previews,
        "conflicts": conflict_count,
        "skipped": skip_count,
        "tree": tree_structure,
    }


def delete_empty_dirs(path: Path) -> None:
    """递归删除空目录（从子目录到父目录）"""
    if not path.exists():
        return
    
    # 先递归删除子目录中的空目录
    for child in path.iterdir():
        if child.is_dir():
            delete_empty_dirs(child)
    
    # 检查当前目录是否为空
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            logger.info(f"删除空目录: {path}")
    except Exception as exc:
        logger.warning(f"删除目录失败 {path}: {exc}")


def execute_format(
    artist: str, album_ids: list[int] | None = None, track_ids: list[int] | None = None
) -> dict:
    preview = preview_format(artist, album_ids, track_ids)
    moved = errors = skipped = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine artist for logging (use first track's artist if formatting by tracks)
    if track_ids and len(track_ids) > 0:
        artist = get_artist_by_track_id(track_ids[0]) or "Unknown"

    # 收集所有原目录以便后续检查是否为空
    original_dirs = set()

    for item in preview["items"]:
        # Skip already formatted files and mark them as organized
        if item["status"] == "skip":
            skipped += 1
            # Mark as organized and not pending if not already
            db = get_db()
            existing = db.execute(
                "SELECT organized, pending FROM tracks WHERE id=?", (item["track_id"],)
            ).fetchone()
            if existing and (existing["organized"] == 0 or existing["pending"] == 1):
                db.execute(
                    "UPDATE tracks SET organized=1, pending=0 WHERE id=?", (item["track_id"],)
                )
            continue
        
        # Skip conflict files
        if item["status"] == "conflict":
            skipped += 1
            continue

        src = Path(item["old_path"])
        dst = Path(item["new_path"])
        try:
            # 记录原目录
            original_dirs.add(src.parent)
            
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            update_track_path_and_name(item["track_id"], str(dst), dst.name)
            moved += 1
        except Exception as exc:
            logger.error("move failed %s -> %s: %s", src, dst, exc)
            add_op_log(now, "error", f"移动失败：{src} → {dst}: {exc}")
            errors += 1

    # 删除空目录（从子目录到父目录递归删除）
    for original_dir in original_dirs:
        delete_empty_dirs(original_dir)

    # mark artist as organized if all albums done
    all_org = count_tracks_by_artist_with_status(artist, 0, 0) == 0

    msg = (
        f"格式化完成：{artist} 移动 {moved} 个文件，跳过 {skipped} 个，{errors} 个失败"
    )
    add_op_log(now, "move", msg)
    commit()
    logger.info(msg)
    return {"moved": moved, "skipped": skipped, "errors": errors, "organized": all_org}


def batch_preview_format(artists: list[str]) -> dict:
    """批量预览多个艺术家的格式化结果，串行处理避免Flask上下文问题"""
    results = {}
    total_files = 0
    total_conflicts = 0
    total_skipped = 0

    for artist in artists:
        try:
            result = preview_format(artist)
            results[artist] = result
            total_files += len(result["items"])
            total_conflicts += result["conflicts"]
            total_skipped += result["skipped"]
        except Exception as exc:
            logger.error(f"预览艺术家 {artist} 失败: {exc}")
            results[artist] = {
                "error": str(exc),
                "items": [],
                "conflicts": 0,
                "skipped": 0,
                "tree": {},
            }

    return {
        "results": results,
        "total_files": total_files,
        "total_conflicts": total_conflicts,
        "total_skipped": total_skipped,
        "artists_count": len(artists),
    }


def batch_execute_format(artists: list[str]) -> dict:
    """批量执行多个艺术家的格式化，串行处理避免Flask上下文问题"""
    results = {}
    total_moved = 0
    total_skipped = 0
    total_errors = 0

    for artist in artists:
        try:
            result = execute_format(artist)
            results[artist] = result
            total_moved += result["moved"]
            total_skipped += result["skipped"]
            total_errors += result["errors"]
        except Exception as exc:
            logger.error(f"执行艺术家 {artist} 格式化失败: {exc}")
            results[artist] = {"error": str(exc), "moved": 0, "skipped": 0, "errors": 1}

    return {
        "results": results,
        "total_moved": total_moved,
        "total_skipped": total_skipped,
        "total_errors": total_errors,
        "artists_count": len(artists),
    }
