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
    # extract feat from title if present
    feat_match = re.search(r"\(feat\.?\s*([^)]+)\)", title, re.IGNORECASE)
    if feat_match:
        feat = feat_match.group(1).strip()
        title = title[: feat_match.start()].strip()
        return f"{num:02d}. {title} - feat. {feat}{ext}"
    return f"{num:02d}. {title}{ext}"


def preview_format(
    artist: str, album_ids: list[int] = None, track_ids: list[int] = None
) -> dict:
    previews = []
    conflict_count = 0
    skip_count = 0
    seen_targets: set[str] = set()
    tree_structure = {}

    if track_ids and len(track_ids) > 0:
        rows = get_tracks_by_ids(track_ids)

        for row in rows:
            if row["pending"]:
                continue
            artist_name = row["artist"] or "Unknown"
            album_name = row["album"] or "Unknown"
            artist_dir = safe_dirname(artist_name)
            album_dir = safe_dirname(album_name)
            target_base = str(Path(MUSIC_ROOT) / artist_dir / album_dir)

            new_name = build_target_filename(row)
            target_path = str(Path(target_base) / new_name)

            # Check if already in correct position (manually formatted)
            if row["path"] == target_path and row["filename"] == new_name:
                status = "skip"
                skip_count += 1
            elif target_path in seen_targets:
                status = "conflict"
                conflict_count += 1
                stem = Path(new_name).stem
                sufx = Path(new_name).suffix
                i = 1
                while target_path in seen_targets:
                    new_name = f"{stem} ({i}){sufx}"
                    target_path = str(Path(target_base) / new_name)
                    i += 1
            else:
                status = "normal"
            seen_targets.add(target_path)

            # Build tree structure
            if artist_name not in tree_structure:
                tree_structure[artist_name] = {}
            if album_name not in tree_structure[artist_name]:
                tree_structure[artist_name][album_name] = {
                    "dir": f"{artist_dir}/{album_dir}",
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
    else:
        album_ids = album_ids or []
        for alb_id in album_ids:
            rows = get_tracks_by_artist_and_album_id(artist, alb_id)
            album_name = rows[0]["album"] if rows else str(alb_id)
            artist_dir = safe_dirname(artist)
            album_dir = safe_dirname(album_name)
            target_base = str(Path(MUSIC_ROOT) / artist_dir / album_dir)

            # Initialize tree structure for this artist/album
            if artist not in tree_structure:
                tree_structure[artist] = {}
            if album_name not in tree_structure[artist]:
                tree_structure[artist][album_name] = {
                    "dir": f"{artist_dir}/{album_dir}",
                    "tracks": [],
                }

            for row in rows:
                if row["pending"]:
                    continue
                new_name = build_target_filename(row)
                target_path = str(Path(target_base) / new_name)

                # Check if already in correct position (manually formatted)
                if row["path"] == target_path and row["filename"] == new_name:
                    status = "skip"
                    skip_count += 1
                elif target_path in seen_targets:
                    status = "conflict"
                    conflict_count += 1
                    stem = Path(new_name).stem
                    sufx = Path(new_name).suffix
                    i = 1
                    while target_path in seen_targets:
                        new_name = f"{stem} ({i}){sufx}"
                        target_path = str(Path(target_base) / new_name)
                        i += 1
                else:
                    status = "normal"
                seen_targets.add(target_path)

                # Add to tree structure
                tree_structure[artist][album_name]["tracks"].append(
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


def execute_format(
    artist: str, album_ids: list[int] = None, track_ids: list[int] = None
) -> dict:
    preview = preview_format(artist, album_ids, track_ids)
    moved = errors = skipped = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine artist for logging (use first track's artist if formatting by tracks)
    if track_ids and len(track_ids) > 0:
        artist = get_artist_by_track_id(track_ids[0]) or "Unknown"

    for item in preview["items"]:
        # Skip already formatted files and mark them as organized
        if item["status"] == "skip":
            skipped += 1
            # Mark as organized if not already
            db = get_db()
            existing = db.execute(
                "SELECT organized FROM tracks WHERE id=?", (item["track_id"],)
            ).fetchone()
            if existing and existing["organized"] == 0:
                db.execute(
                    "UPDATE tracks SET organized=1 WHERE id=?", (item["track_id"],)
                )
            continue

        src = Path(item["old_path"])
        dst = Path(item["new_path"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            update_track_path_and_name(item["track_id"], str(dst), dst.name)
            moved += 1
        except Exception as exc:
            logger.error("move failed %s -> %s: %s", src, dst, exc)
            add_op_log(now, "error", f"移动失败：{src} → {dst}: {exc}")
            errors += 1

    # mark artist as organized if all albums done
    all_org = count_tracks_by_artist_with_status(artist, 0, 0) == 0

    msg = (
        f"格式化完成：{artist} 移动 {moved} 个文件，跳过 {skipped} 个，{errors} 个失败"
    )
    add_op_log(now, "move", msg)
    commit()
    logger.info(msg)
    return {"moved": moved, "skipped": skipped, "errors": errors, "organized": all_org}
