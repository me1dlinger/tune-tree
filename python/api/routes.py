"""
API 路由模块
"""

from flask import Blueprint, request, jsonify, render_template, abort, Response
from functools import wraps
from pathlib import Path
from datetime import datetime
import base64
import hashlib
import logging
import threading

from config import ACCESS_KEY, MUSIC_ROOT
from utils.metadata import (
    get_cover_b64,
    get_lyrics,
    write_metadata,
    write_cover,
    write_lyrics,
)
from services.scan_service import scan_library
from services.format_service import (
    preview_format,
    execute_format,
    batch_preview_format,
    batch_execute_format,
)
from services.metadata_scraper import MetadataScraper
from repository.track_repository import (
    get_track_by_id,
    get_track_by_path,
    get_track_by_filename_and_artist,
    get_track_by_filename_and_album,
    get_track_by_filename,
    get_tracks_by_artist_and_album,
    get_pending_tracks,
    get_duplicate_tracks,
    get_artists,
    get_albums_by_artist,
    get_artist_full_info,
    count_total_tracks,
    count_total_artists,
    count_total_albums,
    count_pending_tracks,
    count_organized_artists,
    count_organized_albums,
    count_duplicate_groups,
    count_tracks_by_extension,
    get_scan_meta,
    get_scan_status,
    set_scan_running,
    set_scan_finished,
    get_op_logs,
    clear_op_logs,
    add_op_log,
    delete_track_by_id,
    update_track_metadata,
    commit,
)

logger = logging.getLogger("tunetree")
api_bp = Blueprint("api", __name__)

SCAN_TIMEOUT_HOURS = 1


def _check_scan_timeout():
    """检查扫描是否超时，若超时则重置状态"""
    scan_status = get_scan_status()
    if scan_status["scanning"] and scan_status["start_time"]:
        elapsed_hours = (datetime.now().timestamp() - scan_status["start_time"]) / 3600
        if elapsed_hours >= SCAN_TIMEOUT_HOURS:
            logger.warning(f"扫描超时，已运行 {elapsed_hours:.2f} 小时，自动重置状态")
            set_scan_finished()
            return True
    return False


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Token") or request.args.get("token")
        if token != ACCESS_KEY:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


@api_bp.route("/")
def index():
    return render_template("index.html")


# Auth
@api_bp.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    data = request.get_json(force=True)
    token = data.get("token", "")
    if token == ACCESS_KEY:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid key"}), 401


# Scan
@api_bp.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    _check_scan_timeout()

    scan_status = get_scan_status()
    if scan_status["scanning"]:
        return jsonify(
            {"error": "scan_in_progress", "message": "扫描正在进行中，请稍后"}
        ), 409

    if not Path(MUSIC_ROOT).exists():
        return jsonify({"error": f"MUSIC_ROOT '{MUSIC_ROOT}' not found"}), 400

    try:
        set_scan_running(datetime.now().timestamp())
        result = scan_library(MUSIC_ROOT)
        return jsonify(result)
    finally:
        set_scan_finished()


@api_bp.route("/api/scan/status", methods=["GET"])
@require_auth
def api_scan_status():
    timed_out = _check_scan_timeout()
    scan_status = get_scan_status()

    if scan_status["scanning"] and scan_status["start_time"]:
        elapsed = int(datetime.now().timestamp() - scan_status["start_time"])
        return jsonify(
            {"scanning": True, "elapsed_seconds": elapsed, "timed_out": False}
        )
    return jsonify({"scanning": False, "elapsed_seconds": 0, "timed_out": timed_out})


# Artists
@api_bp.route("/api/artists")
@require_auth
def api_artists():
    q = request.args.get("q", "").strip()
    rows = get_artists(q)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/artists/<path:artist>/albums")
@require_auth
def api_artist_albums(artist: str):
    rows = get_albums_by_artist(artist)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/artists/<path:artist>/albums/<path:album>/tracks")
@require_auth
def api_album_tracks(artist: str, album: str):
    rows = get_tracks_by_artist_and_album(artist, album)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/artists/<path:artist>/full")
@require_auth
def api_artist_full(artist: str):
    result = get_artist_full_info(artist)
    return jsonify(result)


from repository.track_repository import get_artist_directory_path

ARTIST_COVER_FILENAME = "cover.jpg"
MAX_ARTIST_COVER_SIZE = 5 * 1024 * 1024


@api_bp.route("/api/artists/<path:artist>/cover", methods=["GET"])
@require_auth
def api_artist_cover_get(artist: str):
    artist_dir = get_artist_directory_path(artist)
    if not artist_dir:
        abort(404)
    
    cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
    if not cover_path.exists():
        abort(404)
    
    import os
    file_mtime = int(cover_path.stat().st_mtime)
    artist_hash = hashlib.md5(artist.encode('utf-8')).hexdigest()[:8]
    etag = f'"{artist_hash}-{file_mtime}"'
    
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    
    with open(cover_path, "rb") as f:
        image_data = f.read()
    
    return Response(
        image_data,
        mimetype="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=0, must-revalidate",
            "ETag": etag,
            "Last-Modified": datetime.fromtimestamp(file_mtime).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
        },
    )


@api_bp.route("/api/artists/<path:artist>/cover", methods=["POST"])
@require_auth
def api_artist_cover_upload(artist: str):
    artist_dir = get_artist_directory_path(artist)
    if not artist_dir:
        return jsonify({"error": "artist directory not found"}), 404
    
    if "cover" not in request.files:
        return jsonify({"error": "cover file required"}), 400
    
    f = request.files["cover"]
    if not f.filename:
        return jsonify({"error": "cover file required"}), 400
    
    mime = f.mimetype
    if mime not in ("image/jpeg", "image/png"):
        return jsonify({"error": "only JPEG/PNG format supported"}), 400
    
    image_data = f.read()
    if len(image_data) > MAX_ARTIST_COVER_SIZE:
        return jsonify({"error": "cover file too large (max 5MB)"}), 400
    
    try:
        from PIL import Image
        import io
        
        img = Image.open(io.BytesIO(image_data))
        if img.format != "JPEG":
            img = img.convert("RGB")
        
        cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
        img.save(cover_path, "JPEG", quality=90)
        
    except Exception as exc:
        logger.error("artist cover write error %s: %s", artist, exc)
        return jsonify({"error": str(exc)}), 500
    
    return jsonify({"ok": True, "path": str(cover_path)})


@api_bp.route("/api/artists/<path:artist>/cover/exists", methods=["GET"])
@require_auth
def api_artist_cover_exists(artist: str):
    artist_dir = get_artist_directory_path(artist)
    if not artist_dir:
        return jsonify({"exists": False})
    
    cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
    exists = cover_path.exists()
    
    return jsonify({"exists": exists})


from services.netease_api import NeteaseApi


@api_bp.route("/api/artists/<path:artist>/scrape-cover", methods=["POST"])
@require_auth
def api_artist_scrape_cover(artist: str):
    artist_dir = get_artist_directory_path(artist)
    if not artist_dir:
        return jsonify({"error": "artist directory not found"}), 404
    
    try:
        image_data = NeteaseApi.download_artist_avatar(artist)
        
        if not image_data or len(image_data) < 1000:
            return jsonify({"error": "failed to fetch artist avatar from netease"}), 502
        
        if len(image_data) > MAX_ARTIST_COVER_SIZE:
            return jsonify({"error": "cover file too large (max 5MB)"}), 400
        
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(image_data))
            if img.format != "JPEG":
                img = img.convert("RGB")
            
            cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
            img.save(cover_path, "JPEG", quality=90)
            
        except Exception as exc:
            logger.error("artist cover save error %s: %s", artist, exc)
            return jsonify({"error": str(exc)}), 500
        
        return jsonify({"ok": True, "path": str(cover_path)})
        
    except Exception as exc:
        logger.error("netease api error: %s", exc)
        return jsonify({"error": f"netease api failed: {exc}"}), 502


# Cover art
@api_bp.route("/api/cover/<int:track_id>")
@require_auth
def api_cover(track_id: int):
    row = get_track_by_id(track_id)
    if not row or not row["has_cover"]:
        abort(404)
    data = get_cover_b64(row["path"])
    if not data:
        abort(404)
    # strip data URI prefix and return raw image
    header, b64 = data.split(",", 1)
    mime = header.split(":")[1].split(";")[0]

    # Get file mtime for cache validation
    import os

    file_mtime = int(os.path.getmtime(row["path"]))

    # Use ETag for cache validation instead of long-term caching
    etag = f'"{track_id}-{file_mtime}"'

    # Check If-None-Match header
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    return Response(
        base64.b64decode(b64),
        mimetype=mime,
        headers={
            "Cache-Control": "public, max-age=0, must-revalidate",
            "ETag": etag,
            "Last-Modified": datetime.fromtimestamp(file_mtime).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
        },
    )


# Track detail
@api_bp.route("/api/tracks/<int:track_id>")
@require_auth
def api_track(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    d = dict(row)
    d["lyrics"] = get_lyrics(row["path"]) if row["has_lyrics"] else None
    return jsonify(d)


@api_bp.route("/api/tracks/<int:track_id>", methods=["DELETE"])
@require_auth
def api_track_delete(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    delete_track_by_id(track_id)
    commit()
    return jsonify({"ok": True, "deleted": track_id})


MAX_COVER_SIZE = 5 * 1024 * 1024


@api_bp.route("/api/tracks/<int:track_id>/metadata", methods=["PUT"])
@require_auth
def api_track_update_metadata(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    data = request.get_json(force=True)
    if not data:
        return jsonify({"ok": True, "updated": {}})
    try:
        updated = write_metadata(row["path"], data)
    except Exception as exc:
        logger.error("metadata write error track %d: %s", track_id, exc)
        return jsonify({"error": str(exc)}), 500
    if updated:
        update_track_metadata(track_id, updated)
        if any(k in updated for k in ("artist", "album", "title")):
            update_track_metadata(track_id, {"organized": 0})
        commit()
    return jsonify({"ok": True, "updated": updated})


@api_bp.route("/api/tracks/<int:track_id>/cover", methods=["PUT"])
@require_auth
def api_track_update_cover(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    if "cover" not in request.files:
        return jsonify({"error": "cover file required"}), 400
    f = request.files["cover"]
    if not f.filename:
        return jsonify({"error": "cover file required"}), 400
    mime = f.mimetype
    if mime not in ("image/jpeg", "image/png"):
        return jsonify({"error": "only JPEG/PNG format supported"}), 400
    image_data = f.read()
    if len(image_data) > MAX_COVER_SIZE:
        return jsonify({"error": "cover file too large (max 5MB)"}), 400
    try:
        write_cover(row["path"], image_data, mime)
    except Exception as exc:
        logger.error("cover write error track %d: %s", track_id, exc)
        return jsonify({"error": str(exc)}), 500
    update_track_metadata(track_id, {"has_cover": 1})
    commit()
    return jsonify({"ok": True})


@api_bp.route("/api/tracks/<int:track_id>/lyrics", methods=["PUT"])
@require_auth
def api_track_update_lyrics(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    data = request.get_json(force=True)
    lyrics = data.get("lyrics", "")
    try:
        write_lyrics(row["path"], lyrics)
    except Exception as exc:
        logger.error("lyrics write error track %d: %s", track_id, exc)
        return jsonify({"error": str(exc)}), 500
    has_lyrics = 0 if lyrics == "" else 1
    update_track_metadata(track_id, {"has_lyrics": has_lyrics})
    commit()
    return jsonify({"ok": True})


@api_bp.route("/api/tracks/batch-delete", methods=["POST"])
@require_auth
def api_tracks_batch_delete():
    data = request.get_json(force=True)
    track_ids = data.get("track_ids", [])
    if not track_ids:
        return jsonify({"error": "track_ids required"}), 400
    for track_id in track_ids:
        delete_track_by_id(track_id)
    commit()
    return jsonify({"ok": True, "deleted_count": len(track_ids)})


# Track by path
@api_bp.route("/api/tracks/by-path")
@require_auth
def api_track_by_path():
    rel_path = request.args.get("path", "").strip()

    if not rel_path:
        return jsonify({"error": "path required"}), 400

    # Normalize path separators for Windows
    rel_path_normalized = (
        rel_path.replace("/", "\\") if "\\" in MUSIC_ROOT else rel_path
    )
    full_path = str(Path(MUSIC_ROOT) / rel_path.lstrip("/"))
    full_path_normalized = str(Path(MUSIC_ROOT) / rel_path_normalized.lstrip("/"))

    # Try exact match first
    row = get_track_by_path(full_path)

    # Try normalized path (different separator)
    if not row and full_path != full_path_normalized:
        row = get_track_by_path(full_path_normalized)

    # Try to find by filename in the same directory structure
    if not row:
        filename = Path(rel_path).name
        # Extract potential artist/album from path
        parts = Path(rel_path).parts
        if len(parts) >= 2:
            potential_album = (
                parts[-2]
                if parts[-1] != filename
                else parts[-2]
                if len(parts) > 2
                else None
            )
            potential_artist = (
                parts[-3]
                if parts[-1] != filename
                else parts[-2]
                if len(parts) > 3
                else None
            )

            # Search by filename and artist if available
            if potential_artist:
                row = get_track_by_filename_and_artist(filename, potential_artist)
            elif potential_album:
                row = get_track_by_filename_and_album(filename, potential_album)

        # Last resort: search by filename only
        if not row:
            row = get_track_by_filename(filename)

    if not row:
        abort(404)

    d = dict(row)
    d["lyrics"] = get_lyrics(row["path"]) if row["has_lyrics"] else None
    return jsonify(d)


# Files browser
@api_bp.route("/api/files")
@require_auth
def api_files():
    rel = request.args.get("path", "").lstrip("/")
    base = Path(MUSIC_ROOT)
    cur = (base / rel).resolve()
    if not str(cur).startswith(str(base.resolve())):
        abort(403)
    if not cur.exists():
        abort(404)

    limit = request.args.get("limit", type=int, default=500)
    offset = request.args.get("offset", type=int, default=0)
    sort = request.args.get("sort", default="name")
    search = request.args.get("search", default="").strip().lower()

    try:
        entries = list(cur.iterdir())
    except OSError as e:
        logger.warning(f"Failed to read directory {cur}: {e}")
        entries = []

    entries_data = []
    for entry in entries:
        try:
            stat = entry.stat()
            entries_data.append(
                {
                    "name": entry.name,
                    "path": str(entry.relative_to(base)),
                    "is_dir": entry.is_dir(),
                    "ext": entry.suffix.lower().lstrip(".")
                    if not entry.is_dir()
                    else "dir",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        except OSError:
            continue

    if search:
        entries_data = [e for e in entries_data if search in e["name"].lower()]

    if sort == "date":
        entries_data.sort(key=lambda e: (e["is_dir"], -e["mtime"]), reverse=False)
        dirs = [e for e in entries_data if e["is_dir"]]
        files = [e for e in entries_data if not e["is_dir"]]
        dirs.sort(key=lambda e: -e["mtime"])
        files.sort(key=lambda e: -e["mtime"])
        entries_data = dirs + files
    else:
        entries_data.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    total = len(entries_data)
    page_items = entries_data[offset : offset + limit]

    for item in page_items:
        item["mtime"] = datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M")

    return jsonify(
        {
            "path": rel,
            "items": page_items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


# Stats
@api_bp.route("/api/stats")
@require_auth
def api_stats():
    total_tracks = count_total_tracks()
    total_artists = count_total_artists()
    total_albums = count_total_albums()
    pending_count = count_pending_tracks()
    org_artists = count_organized_artists()
    org_albums = count_organized_albums()
    dupes = count_duplicate_groups()
    flac_count = count_tracks_by_extension(".flac")
    mp3_count = count_tracks_by_extension(".mp3")
    last_scan = get_scan_meta("last_scan") or "—"

    # 获取扫描状态
    _check_scan_timeout()
    scan_status = get_scan_status()
    scan_info = {
        "scanning": scan_status["scanning"],
        "scan_timed_out": False,
        "scan_elapsed_seconds": 0,
    }
    if scan_status["scanning"] and scan_status["start_time"]:
        scan_info["scan_elapsed_seconds"] = int(
            datetime.now().timestamp() - scan_status["start_time"]
        )
        # 检查是否即将超时（超过55分钟视为即将超时）
        if scan_info["scan_elapsed_seconds"] > 55 * 60:
            scan_info["scan_timed_out"] = True

    return jsonify(
        {
            "total_tracks": total_tracks,
            "total_artists": total_artists,
            "total_albums": total_albums,
            "pending_count": pending_count,
            "org_artists": org_artists,
            "org_albums": org_albums,
            "duplicates": dupes,
            "flac_count": flac_count,
            "mp3_count": mp3_count,
            "last_scan": last_scan,
            "scan_info": scan_info,
        }
    )


# Pending files
@api_bp.route("/api/pending")
@require_auth
def api_pending():
    rows = get_pending_tracks()
    return jsonify([dict(r) for r in rows])


# Duplicates
@api_bp.route("/api/duplicates")
@require_auth
def api_duplicates():
    rows = get_duplicate_tracks()
    return jsonify([dict(r) for r in rows])


# Format preview & execute
@api_bp.route("/api/format/preview", methods=["POST"])
@require_auth
def api_format_preview():
    data = request.get_json(force=True)
    artist = data.get("artist")
    album_ids = data.get("album_ids")
    track_ids = data.get("track_ids", [])
    if track_ids and len(track_ids) > 0:
        result = preview_format(artist, track_ids=track_ids)
    elif artist and album_ids is not None and len(album_ids) > 0:
        result = preview_format(artist, album_ids)
    elif artist:
        result = preview_format(artist)
    else:
        return jsonify(
            {"error": "artist and album_ids required, or track_ids required"}
        ), 400
    return jsonify(result)


@api_bp.route("/api/format/execute", methods=["POST"])
@require_auth
def api_format_execute():
    data = request.get_json(force=True)
    artist = data.get("artist")
    album_ids = data.get("album_ids")
    track_ids = data.get("track_ids", [])
    if track_ids and len(track_ids) > 0:
        result = execute_format(artist, track_ids=track_ids)
    elif artist and album_ids is not None and len(album_ids) > 0:
        result = execute_format(artist, album_ids)
    elif artist:
        result = execute_format(artist)
    else:
        return jsonify(
            {"error": "artist and album_ids required, or track_ids required"}
        ), 400
    return jsonify(result)


@api_bp.route("/api/format/batch-preview", methods=["POST"])
@require_auth
def api_format_batch_preview():
    data = request.get_json(force=True)
    artists = data.get("artists", [])
    if not artists or len(artists) == 0:
        return jsonify({"error": "artists list is required"}), 400
    result = batch_preview_format(artists)
    return jsonify(result)


@api_bp.route("/api/format/batch-execute", methods=["POST"])
@require_auth
def api_format_batch_execute():
    data = request.get_json(force=True)
    artists = data.get("artists", [])
    if not artists or len(artists) == 0:
        return jsonify({"error": "artists list is required"}), 400
    result = batch_execute_format(artists)
    return jsonify(result)


# Logs
@api_bp.route("/api/logs")
@require_auth
def api_logs():
    rows = get_op_logs(200)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/logs", methods=["DELETE"])
@require_auth
def api_logs_clear():
    clear_op_logs()
    commit()
    return jsonify({"ok": True})


# === 元数据刮削相关接口 ===

scraper = MetadataScraper()


@api_bp.route("/api/tracks/<int:track_id>/scrape", methods=["POST"])
@require_auth
def api_scrape_metadata(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    
    data = request.get_json(force=True) or {}
    preferred_api = data.get("preferred_api")
    
    current_meta = {
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
    }
    
    add_op_log(datetime.now().isoformat(), "scrape_start", f"开始刮削元数据: {row['filename']}")
    
    try:
        scraped_data = scraper.scrape(row["path"], current_meta, preferred_api)
        
        if scraped_data:
            add_op_log(datetime.now().isoformat(), "scrape_success", 
                      f"成功从 {scraped_data['_source']} 获取元数据: {row['filename']}")
            return jsonify({
                "ok": True,
                "original": current_meta,
                "scraped": scraped_data
            })
        else:
            add_op_log(datetime.now().isoformat(), "scrape_fail", 
                      f"未能找到匹配的元数据: {row['filename']}")
            return jsonify({"ok": False, "error": "未找到匹配的元数据"})
    except Exception as e:
        logger.error(f"刮削元数据失败: {e}")
        add_op_log(datetime.now().isoformat(), "scrape_error", 
                  f"刮削元数据出错: {row['filename']} - {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/tracks/<int:track_id>/apply-scrape", methods=["POST"])
@require_auth
def api_apply_scraped_metadata(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    
    data = request.get_json(force=True)
    if not data:
        return jsonify({"ok": False, "error": "缺少数据"}), 400
    
    add_op_log(datetime.now().isoformat(), "apply_scrape_start", 
              f"开始应用刮削的元数据: {row['filename']}")
    
    try:
        # 准备元数据更新
        meta_fields = {}
        for key in ["title", "artist", "album", "album_artist", "year", "track_num"]:
            if key in data and data[key] is not None:
                meta_fields[key] = data[key]
        
        # 更新元数据标签
        updated = {}
        if meta_fields:
            updated = write_metadata(row["path"], meta_fields)
            if updated:
                update_track_metadata(track_id, updated)
                if any(k in updated for k in ("artist", "album", "title")):
                    update_track_metadata(track_id, {"organized": 0})
        
        # 更新封面
        cover_updated = False
        if data.get("_cover_data"):
            cover_data = base64.b64decode(data["_cover_data"])
            write_cover(row["path"], cover_data, "image/jpeg")
            update_track_metadata(track_id, {"has_cover": 1})
            cover_updated = True
        
        # 更新歌词
        lyrics_updated = False
        if data.get("lyrics") is not None:
            write_lyrics(row["path"], data["lyrics"])
            has_lyrics = 1 if data["lyrics"] else 0
            update_track_metadata(track_id, {"has_lyrics": has_lyrics})
            lyrics_updated = True
        
        commit()
        
        add_op_log(datetime.now().isoformat(), "apply_scrape_success", 
                  f"成功应用元数据: {row['filename']}")
        
        return jsonify({
            "ok": True,
            "updated": updated,
            "cover_updated": cover_updated,
            "lyrics_updated": lyrics_updated
        })
    except Exception as e:
        logger.error(f"应用刮削的元数据失败: {e}")
        add_op_log(datetime.now().isoformat(), "apply_scrape_error",
                  f"应用元数据出错: {row['filename']} - {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/tracks/<int:track_id>/scrape-all", methods=["POST"])
@require_auth
def api_scrape_all(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)

    current_meta = {
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
    }

    add_op_log(datetime.now().isoformat(), "scrape_all_start", f"开始批量刮削: {row['filename']}")

    try:
        results = scraper.search_all_apis(row["path"], current_meta, max_per_api=3)
        add_op_log(datetime.now().isoformat(), "scrape_all_success",
                  f"批量刮削完成: {row['filename']}")
        return jsonify({
            "ok": True,
            "original": current_meta,
            "results": results
        })
    except Exception as e:
        logger.error(f"批量刮削失败: {e}")
        add_op_log(datetime.now().isoformat(), "scrape_all_error",
                  f"批量刮削出错: {row['filename']} - {str(e)}")
        return jsonify({"ok": False, "error": str(e)}), 500
