"""
API 路由模块
"""

from flask import (
    Blueprint,
    request,
    jsonify,
    send_from_directory,
    abort,
    Response,
    send_file,
)
from functools import wraps
from pathlib import Path
from datetime import datetime
import base64
import hashlib
import logging
import re
import threading
import zipfile
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import ACCESS_KEY, MUSIC_ROOT
from utils.metadata import (
    get_cover_b64,
    get_lyrics,
    read_metadata,
    write_metadata,
    write_cover,
    write_lyrics,
    normalize_str,
)
from utils.formatting import get_relative_path
from services.scan_service import scan_library
from services.format_service import (
    preview_format,
    execute_format,
    batch_preview_format,
    batch_execute_format,
)
from services.metadata_scraper import MetadataScraper
from services.netease_api import NeteaseApi
from repository.track_repository import (
    get_track_by_id,
    get_track_by_path,
    get_track_by_filename_and_artist,
    get_tracks_by_ids,
    get_track_by_filename_and_album,
    get_track_by_filename,
    get_tracks_by_artist_id,
    get_tracks_by_album_id,
    get_pending_tracks,
    get_duplicate_tracks,
    get_artists,
    get_artist_full_info_by_id,
    get_artist_directory_path_by_id,
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
    insert_track,
    recalc_pending,
    commit,
)

logger = logging.getLogger("tunetree")
api_bp = Blueprint("api", __name__)

SCAN_TIMEOUT_HOURS = 1
scraper = MetadataScraper()


def _relink_track_artist_album(track_id: int):
    from repository.artist_repository import (
        ensure_artist,
        get_artist_by_name,
        delete_artist,
    )
    from repository.album_repository import (
        ensure_album,
        get_album_by_title_and_artist,
        delete_album,
        get_album_by_id,
    )

    row = get_track_by_id(track_id)
    if not row:
        return
    new_artist_name = row["artist"]
    new_album_name = row["album"]
    if not new_artist_name:
        return

    old_artist_id = row["artist_id"]
    old_album_id = row["album_id"]

    old_album = get_album_by_id(old_album_id) if old_album_id else None
    old_artist = get_artist_by_id(old_artist_id) if old_artist_id else None

    new_artist_id = ensure_artist(new_artist_name)
    new_album_id = None
    if new_album_name:
        new_album_id = ensure_album(new_album_name, new_artist_id, year=row["year"])

    update_track_metadata(
        track_id,
        {
            "artist_id": new_artist_id,
            "album_id": new_album_id,
            "track_artist": new_artist_name,
        },
    )

    if old_album_id and old_album_id != new_album_id:
        remaining = get_tracks_by_album_id(old_album_id)
        if not remaining:
            delete_album(old_album_id)

    if old_artist_id and old_artist_id != new_artist_id:
        if old_album_id and old_album_id != new_album_id:
            remaining_in_old_album = (
                get_db()
                .execute(
                    "SELECT COUNT(*) FROM tracks WHERE album_id=? AND artist_id=?",
                    (old_album_id, old_artist_id),
                )
                .fetchone()[0]
            )
            if remaining_in_old_album == 0 and old_album:
                remaining_album_tracks = get_tracks_by_album_id(old_album_id)
                if not remaining_album_tracks:
                    delete_album(old_album_id)

        remaining_albums = get_albums_by_artist_id(old_artist_id)
        if not remaining_albums:
            delete_artist(old_artist_id)


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
    return send_from_directory("static", "index.html")


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


@api_bp.route("/api/artists/<int:artist_id>/albums")
@require_auth
def api_artist_albums(artist_id: int):
    rows = get_albums_by_artist_id(artist_id)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/albums/<int:album_id>/tracks")
@require_auth
def api_album_tracks(album_id: int):
    rows = get_tracks_by_album_id(album_id)
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/tracks/<int:track_id>/download")
@require_auth
def api_track_download(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    artist = row["artist"]
    title = row["title"]
    ext = row["ext"]
    download_name = f"{safe_filename(artist)} - {safe_filename(title)}.{ext}"
    track_path = Path(row["path"])
    if not track_path.exists():
        abort(404)
    return send_file(track_path, as_attachment=True, download_name=download_name)


@api_bp.route("/api/albums/<int:album_id>/download")
@require_auth
def api_album_download(album_id: int):
    from repository.album_repository import get_album_by_id

    album = get_album_by_id(album_id)
    if not album:
        abort(404)
    artist_row = get_artist_by_id(album["artist_id"]) if album["artist_id"] else None
    artist_name = artist_row["name"] if artist_row else "Unknown"
    album_title = album["title"] or "Unknown"

    rows = get_tracks_by_album_id(album_id)
    if not rows:
        abort(404)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for row in rows:
            track_path = Path(row["path"])
            if track_path.exists():
                arcname = f"{track_path.name}"
                zipf.write(track_path, arcname)

    zip_buffer.seek(0)
    safe_artist = safe_filename(artist_name)
    safe_album = safe_filename(album_title)
    zipname = f"{safe_artist} - {safe_album}.zip"
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zipname,
    )


@api_bp.route("/api/artists/<int:artist_id>/download")
@require_auth
def api_artist_download(artist_id: int):
    artist_row = get_artist_by_id(artist_id)
    if not artist_row:
        abort(404)
    artist_name = artist_row["name"]

    rows = get_tracks_by_artist_id(artist_id)
    if not rows:
        abort(404)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for row in rows:
            track_path = Path(row["path"])
            if track_path.exists():
                album_folder = track_path.parent.name
                arcname = f"{album_folder}/{track_path.name}"
                zipf.write(track_path, arcname)

    zip_buffer.seek(0)
    zipname = f"{safe_filename(artist_name)}.zip"

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zipname,
    )


@api_bp.route("/api/tracks/download-batch", methods=["POST"])
@require_auth
def api_tracks_batch_download():
    data = request.get_json(force=True)
    track_ids = data.get("track_ids", [])
    if not track_ids:
        return jsonify({"error": "no tracks specified"}), 400

    rows = []
    for tid in track_ids:
        row = get_track_by_id(tid)
        if row:
            rows.append(row)

    if not rows:
        abort(404)

    if len(rows) == 1:
        track_path = Path(rows[0]["path"])
        if track_path.exists():
            return send_file(
                track_path, as_attachment=True, download_name=track_path.name
            )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for row in rows:
            track_path = Path(row["path"])
            if track_path.exists():
                artist = safe_filename(row["artist"] or "Unknown")
                album = safe_filename(row["album"] or "Unknown")
                track_name = track_path.name
                arcname = f"{artist}/{album}/{track_name}"
                zipf.write(track_path, arcname)

    zip_buffer.seek(0)

    if len(rows) <= 3:
        names = " ".join([Path(r["path"]).stem for r in rows[:3]])
        zipname = f"{names}.zip"
    else:
        names = " ".join([Path(r["path"]).stem for r in rows[:3]])
        zipname = f"{names}...zip"

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zipname,
    )


@api_bp.route("/api/files/download")
@require_auth
def api_files_download():
    path = request.args.get("path", "").lstrip("/")
    if not path:
        abort(400)

    base = Path(MUSIC_ROOT)
    file_path = (base / path).resolve()
    if not str(file_path).startswith(str(base.resolve())):
        abort(403)

    if not file_path.exists():
        abort(404)

    if file_path.is_file():
        return send_file(file_path, as_attachment=True, download_name=file_path.name)
    else:
        zip_buffer = io.BytesIO()
        dir_name = file_path.name
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(file_path):
                dirs.sort()
                for filename in sorted(files):
                    file_full = Path(root) / filename
                    arcname = str(file_full.relative_to(file_path))
                    zipf.write(file_full, arcname)

        zip_buffer.seek(0)
        zipname = f"{dir_name}.zip"
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=zipname,
        )


def safe_filename(name: str) -> str:
    if not name:
        return "Unknown"
    import re

    result = re.sub(r'[\\/:*?"<>|]', "_", name)
    result = re.sub(r"[\x00-\x1f\x7f]", "", result)
    result = result.strip()
    if result.startswith("-"):
        result = "_" + result[1:]
    result = re.sub(r"\.+$", "_", result)
    return result or "Unknown"


@api_bp.route("/api/artists/<int:artist_id>/full")
@require_auth
def api_artist_full(artist_id: int):
    result = get_artist_full_info_by_id(artist_id)
    if not result:
        abort(404)
    return jsonify(result)


from repository.album_repository import (
    get_album_by_id,
    get_albums_by_artist_id,
    update_album as update_album_repo,
)
from repository.artist_repository import get_artist_by_id
from models.db import get_db

ARTIST_COVER_FILENAME = "cover.jpg"
ALBUM_COVER_FILENAME = "cover.jpg"
MAX_ARTIST_COVER_SIZE = 5 * 1024 * 1024


@api_bp.route("/api/artists/<int:artist_id>/cover", methods=["GET"])
@require_auth
def api_artist_cover_get(artist_id: int):
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        abort(404)
    cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
    if not cover_path.exists():
        abort(404)

    file_mtime = int(cover_path.stat().st_mtime)
    etag = f'"{artist_id}-{file_mtime}"'

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


@api_bp.route("/api/artists/<int:artist_id>/cover", methods=["POST"])
@require_auth
def api_artist_cover_upload(artist_id: int):
    artist_dir = get_artist_directory_path_by_id(artist_id)
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
        logger.error("artist cover write error %d: %s", artist_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "path": str(cover_path)})


@api_bp.route("/api/artists/<int:artist_id>/cover/exists", methods=["GET"])
@require_auth
def api_artist_cover_exists(artist_id: int):
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        return jsonify({"exists": False})

    cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
    exists = cover_path.exists()

    return jsonify({"exists": exists})


@api_bp.route("/api/artists/<int:artist_id>/cover", methods=["DELETE"])
@require_auth
def api_artist_cover_delete(artist_id: int):
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        return jsonify({"error": "artist directory not found"}), 404

    cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
    if not cover_path.exists():
        return jsonify({"error": "cover file not found"}), 404

    try:
        cover_path.unlink()
    except Exception as exc:
        logger.error("artist cover delete error %d: %s", artist_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True})


@api_bp.route("/api/albums/<int:album_id>/cover", methods=["GET"])
@require_auth
def api_album_cover_get(album_id: int):
    album = get_album_by_id(album_id)
    if not album:
        abort(404)

    artist_row = get_artist_by_id(album["artist_id"]) if album["artist_id"] else None
    if not artist_row:
        abort(404)

    album_dir = Path(MUSIC_ROOT) / artist_row["dir_name"] / album["dir_name"]
    cover_path = album_dir / ALBUM_COVER_FILENAME

    if not cover_path.exists():
        first_track = (
            get_db()
            .execute(
                "SELECT path FROM tracks WHERE album_id=? AND has_cover=1 ORDER BY disc_num, track_num LIMIT 1",
                (album_id,),
            )
            .fetchone()
        )
        if first_track:
            from utils.metadata import extract_cover_to_file

            if extract_cover_to_file(first_track["path"], str(cover_path)):
                update_album_repo(album_id, cover_path=str(cover_path))
            else:
                abort(404)
        else:
            abort(404)

    file_mtime = int(cover_path.stat().st_mtime)
    etag = f'"{album_id}-{file_mtime}"'

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


@api_bp.route("/api/albums/<int:album_id>/cover/exists", methods=["GET"])
@require_auth
def api_album_cover_exists(album_id: int):
    album = get_album_by_id(album_id)
    if not album:
        return jsonify({"exists": False})

    artist_row = get_artist_by_id(album["artist_id"]) if album["artist_id"] else None
    if not artist_row:
        return jsonify({"exists": False})

    cover_path = (
        Path(MUSIC_ROOT)
        / artist_row["dir_name"]
        / album["dir_name"]
        / ALBUM_COVER_FILENAME
    )
    return jsonify({"exists": cover_path.exists()})


@api_bp.route("/api/albums/<int:album_id>/cover", methods=["POST"])
@require_auth
def api_album_cover_upload(album_id: int):
    album = get_album_by_id(album_id)
    if not album:
        return jsonify({"error": "album not found"}), 404

    artist_row = get_artist_by_id(album["artist_id"]) if album["artist_id"] else None
    if not artist_row:
        return jsonify({"error": "artist not found"}), 404

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
        import io as _io

        img = Image.open(_io.BytesIO(image_data))
        if img.format != "JPEG":
            img = img.convert("RGB")

        album_dir = Path(MUSIC_ROOT) / artist_row["dir_name"] / album["dir_name"]
        album_dir.mkdir(parents=True, exist_ok=True)
        cover_path = album_dir / ALBUM_COVER_FILENAME
        img.save(cover_path, "JPEG", quality=90)

        update_album_repo(album_id, cover_path=str(cover_path))

    except Exception as exc:
        logger.error("album cover write error %d: %s", album_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "path": str(cover_path)})


@api_bp.route("/api/artists/<int:artist_id>/scrape-cover", methods=["POST"])
@require_auth
def api_artist_scrape_cover(artist_id: int):
    artist_row = get_artist_by_id(artist_id)
    if not artist_row:
        abort(404)
    artist_name = artist_row["name"]
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        return jsonify({"error": "artist directory not found"}), 404
    image_data, successful_artist = scraper.scrape_artist_avatar(artist_name)

    if not image_data or len(image_data) < 1000:
        return jsonify(
            {"error": f"failed to fetch artist avatar for: {artist_name}"}
        ), 502

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
        logger.error("artist cover save error %d: %s", artist_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "ok": True,
            "path": str(cover_path),
            "artist": successful_artist or artist_name,
        }
    )


# Lyrics search and fetch
@api_bp.route("/api/lyrics/search", methods=["POST"])
@require_auth
def api_lyrics_search():
    data = request.get_json(force=True) or {}
    keyword = data.get("keyword", "")
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400

    try:
        results = NeteaseApi.search_song(keyword)
        formatted = []
        for r in results:
            formatted.append(
                {
                    "id": r["idOrMd5"],
                    "title": r["songName"],
                    "artist": r["singer"],
                    "album": r.get("album", ""),
                    "duration": r["duration"],
                    "source": "netease",
                }
            )
        return jsonify({"ok": True, "results": formatted})
    except Exception as e:
        logger.error(f"搜索歌词失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/lyrics/<song_id>", methods=["GET"])
@require_auth
def api_lyrics_fetch(song_id: str):
    try:
        lyrics = NeteaseApi.get_lyrics_by_song_id(song_id)
        return jsonify({"ok": True, "lyrics": lyrics})
    except Exception as e:
        logger.error(f"获取歌词失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


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


AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
    ".opus": "audio/opus",
}


@api_bp.route("/api/tracks/<int:track_id>/audio")
@require_auth
def api_track_audio(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    track_path = Path(row["path"])
    if not track_path.exists():
        abort(404)

    file_size = track_path.stat().st_size
    mime = AUDIO_MIME.get(track_path.suffix.lower(), "audio/mpeg")

    range_header = request.headers.get("Range")
    if range_header:
        byte_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if byte_match:
            start = int(byte_match.group(1))
            end = int(byte_match.group(2)) if byte_match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(track_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            return Response(
                generate(),
                status=206,
                mimetype=mime,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                },
            )

    return send_file(track_path, mimetype=mime)


# Track detail
@api_bp.route("/api/tracks/<int:track_id>")
@require_auth
def api_track(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    d = dict(row)
    d["lyrics"] = get_lyrics(row["path"]) if row["has_lyrics"] else None
    d["relative_path"] = get_relative_path(row["path"], MUSIC_ROOT)
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
            _relink_track_artist_album(track_id)
        recalc_pending(track_id)
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
    recalc_pending(track_id)
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
    recalc_pending(track_id)
    commit()
    return jsonify({"ok": True})


@api_bp.route("/api/tracks/<int:track_id>/export-lrc", methods=["POST"])
@require_auth
def api_track_export_lrc(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    data = request.get_json(force=True)
    lyrics = data.get("lyrics", "")
    if not lyrics:
        return jsonify({"error": "歌词内容为空"}), 400
    track_path = Path(row["path"])
    lrc_path = track_path.with_suffix(".lrc")
    try:
        lrc_path.write_text(lyrics, encoding="utf-8")
    except Exception as exc:
        logger.error("lrc export error track %d: %s", track_id, exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "path": str(lrc_path.name)})


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
        # File not in library — try to read metadata and insert on the fly
        candidate = (
            full_path
            if Path(full_path).exists()
            else (full_path_normalized if Path(full_path_normalized).exists() else None)
        )
        if candidate and Path(candidate).suffix.lower() in (".mp3", ".flac"):
            try:
                meta = read_metadata(candidate)
                stat = Path(candidate).stat()
                missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
                pending = 1 if missing else 0
                missing_str = ",".join(missing) if missing else ""
                artist_name = (
                    normalize_str(meta["artist"]) if meta.get("artist") else None
                )
                album_name = meta.get("album") or ""
                album_artist_name = meta.get("album_artist") or ""

                artist_id = None
                album_id = None
                if artist_name:
                    from repository.artist_repository import ensure_artist
                    from repository.album_repository import ensure_album

                    effective_artist = album_artist_name or artist_name
                    artist_id = ensure_artist(effective_artist)
                    if album_name:
                        album_id = ensure_album(
                            album_name,
                            artist_id,
                            year=meta.get("year"),
                        )

                insert_track(
                    path=candidate,
                    filename=Path(candidate).name,
                    ext=Path(candidate).suffix.lower().lstrip("."),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    ctime=stat.st_ctime,
                    title=meta["title"],
                    artist=artist_name,
                    album=meta["album"],
                    album_artist=meta["album_artist"],
                    year=meta["year"],
                    track_num=meta["track_num"],
                    disc_num=meta["disc_num"],
                    duration=meta["duration"],
                    sample_rate=meta["sample_rate"],
                    bitrate=meta["bitrate"],
                    has_cover=meta["has_cover"],
                    has_lyrics=meta["has_lyrics"],
                    pending=pending,
                    missing_tags=missing_str,
                    scanned_at=time.time(),
                    artist_id=artist_id,
                    album_id=album_id,
                    track_artist=artist_name,
                )
                commit()
                row = get_track_by_path(candidate)
                logger.info(f"Auto-imported track from file browser: {candidate}")
            except Exception as exc:
                logger.warning(f"Auto-import failed for {candidate}: {exc}")

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
    folders_first = request.args.get("folders_first", default="true").lower() == "true"
    search = request.args.get("search", default="").strip().lower()
    recursive = request.args.get("recursive", default="false").lower() == "true"

    # 递归模式：只返回音频文件，不返回目录
    if recursive:
        if not cur.is_dir():
            abort(404)
        try:
            entries_data = []
            for entry in cur.rglob("*"):
                try:
                    if not entry.is_file():
                        continue
                    ext = entry.suffix.lower().lstrip(".")
                    if ext not in ("mp3", "flac"):
                        continue
                    stat = entry.stat()
                    entries_data.append(
                        {
                            "name": entry.name,
                            "path": str(entry.relative_to(base)),
                            "is_dir": False,
                            "ext": ext,
                            "is_audio": True,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        }
                    )
                except OSError:
                    continue
        except OSError as e:
            logger.warning(f"Failed to recursively read directory {cur}: {e}")
            entries_data = []

        if search:
            entries_data = [e for e in entries_data if search in e["name"].lower()]

        if sort == "date":
            entries_data.sort(key=lambda e: -e["mtime"])
        else:
            entries_data.sort(key=lambda e: e["name"].lower())

        total = len(entries_data)
        page_items = entries_data[offset : offset + limit]

        for item in page_items:
            item["mtime"] = datetime.fromtimestamp(item["mtime"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        return jsonify(
            {
                "path": rel,
                "items": page_items,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    # 非递归模式（默认）：保持原有行为，用于目录浏览
    try:
        entries = list(cur.iterdir())
    except OSError as e:
        logger.warning(f"Failed to read directory {cur}: {e}")
        entries = []

    entries_data = []
    for entry in entries:
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
            ext = entry.suffix.lower().lstrip(".") if not is_dir else "dir"
            is_audio = ext in ("mp3", "flac") if not is_dir else False
            entries_data.append(
                {
                    "name": entry.name,
                    "path": str(entry.relative_to(base)),
                    "is_dir": is_dir,
                    "ext": ext,
                    "is_audio": is_audio,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        except OSError:
            continue

    if search:
        entries_data = [e for e in entries_data if search in e["name"].lower()]

    if sort == "date":
        dirs = [e for e in entries_data if e["is_dir"]]
        files = [e for e in entries_data if not e["is_dir"]]
        dirs.sort(key=lambda e: -e["mtime"])
        files.sort(key=lambda e: -e["mtime"])
        if folders_first:
            entries_data = dirs + files
        else:
            entries_data = files + dirs
    else:
        if folders_first:
            entries_data.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        else:
            entries_data.sort(key=lambda e: (e["is_dir"], e["name"].lower()))

    total = len(entries_data)
    page_items = entries_data[offset : offset + limit]

    for item in page_items:
        item["mtime"] = datetime.fromtimestamp(item["mtime"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

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
    result = []
    for row in rows:
        d = dict(row)
        d["relative_path"] = get_relative_path(row["path"], MUSIC_ROOT)
        result.append(d)
    return jsonify(result)


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


@api_bp.route("/api/tracks/<int:track_id>/scrape", methods=["POST"])
@require_auth
def api_scrape_metadata(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)

    data = request.get_json(force=True) or {}
    preferred_api = data.get("preferred_api")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_meta = {
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
    }
    try:
        scraped_data = scraper.scrape(row["path"], current_meta, preferred_api)

        if scraped_data:
            add_op_log(
                now,
                "scrape_success",
                f"成功从 {scraped_data['_source']} 获取元数据: {row['filename']}",
            )
            commit()
            return jsonify(
                {"ok": True, "original": current_meta, "scraped": scraped_data}
            )
        else:
            add_op_log(now, "scrape_fail", f"未能找到匹配的元数据: {row['filename']}")
            commit()
            return jsonify({"ok": False, "error": "未找到匹配的元数据"})
    except Exception as e:
        logger.error(f"刮削元数据失败: {e}")
        add_op_log(now, "scrape_error", f"刮削元数据出错: {row['filename']} - {str(e)}")
        commit()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/tracks/<int:track_id>/apply-scrape", methods=["POST"])
@require_auth
def api_apply_scraped_metadata(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data = request.get_json(force=True)
    if not data:
        return jsonify({"ok": False, "error": "缺少数据"}), 400
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
                    _relink_track_artist_album(track_id)
        cover_updated = False
        if data.get("_cover_data"):
            cover_data = base64.b64decode(data["_cover_data"])
            write_cover(row["path"], cover_data, "image/jpeg")
            update_track_metadata(track_id, {"has_cover": 1})
            cover_updated = True

            album_id = row["album_id"]
            if album_id:
                album = get_album_by_id(album_id)
                if album:
                    artist_row = (
                        get_artist_by_id(album["artist_id"])
                        if album["artist_id"]
                        else None
                    )
                    if artist_row:
                        album_dir = os.path.join(
                            MUSIC_ROOT, artist_row["dir_name"], album["dir_name"]
                        )
                        cover_file_path = os.path.join(album_dir, ALBUM_COVER_FILENAME)
                        file_exists = os.path.exists(cover_file_path)

                        if not album["cover_path"] and not file_exists:
                            from utils.metadata import extract_cover_to_file

                            if extract_cover_to_file(row["path"], cover_file_path):
                                update_album_repo(album_id, cover_path=cover_file_path)
                        elif not album["cover_path"] and file_exists:
                            update_album_repo(album_id, cover_path=cover_file_path)

        # 更新歌词
        lyrics_updated = False
        if data.get("lyrics") is not None:
            write_lyrics(row["path"], data["lyrics"])
            has_lyrics = 1 if data["lyrics"] else 0
            update_track_metadata(track_id, {"has_lyrics": has_lyrics})
            lyrics_updated = True
        add_op_log(now, "apply_scrape_success", f"成功应用元数据: {row['filename']}")
        recalc_pending(track_id)
        commit()
        return jsonify(
            {
                "ok": True,
                "updated": updated,
                "cover_updated": cover_updated,
                "lyrics_updated": lyrics_updated,
            }
        )
    except Exception as e:
        logger.error(f"应用刮削的元数据失败: {e}")
        add_op_log(
            now, "apply_scrape_error", f"应用元数据出错: {row['filename']} - {str(e)}"
        )
        commit()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/tracks/<int:track_id>/scrape-all", methods=["POST"])
@require_auth
def api_scrape_all(track_id: int):
    row = get_track_by_id(track_id)
    if not row:
        abort(404)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    current_meta = {
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "track_num": row["track_num"] if row["track_num"] else "",
        "year": row["year"] if row["year"] else "",
        "filename": row["filename"] if row["filename"] else "",
    }

    # 获取需要排除的结果（使用 idOrMd5）
    exclude_ids = request.json.get("exclude_ids", []) if request.is_json else []

    # 获取用户输入的关键词（来自前端输入框）
    user_input = {}
    if request.is_json:
        user_json = request.json
        if user_json.get("title") and user_json["title"].strip():
            user_input["title"] = user_json["title"].strip()
        if user_json.get("artist") and user_json["artist"].strip():
            user_input["artist"] = user_json["artist"].strip()
        if user_json.get("album") and user_json["album"].strip():
            user_input["album"] = user_json["album"].strip()
        if user_json.get("track_num") and str(user_json["track_num"]).strip():
            user_input["track_num"] = str(user_json["track_num"]).strip()
        if user_json.get("year") and str(user_json["year"]).strip():
            user_input["year"] = str(user_json["year"]).strip()

    try:
        results = scraper.search_all_apis(
            row["path"], current_meta, exclude_ids, user_input
        )
        # add_op_log(now, "scrape_all_success", f"批量搜索完成: {row['filename']}")
        commit()
        return jsonify({"ok": True, "original": current_meta, "results": results})

    except Exception as e:
        logger.error(f"批量搜索失败: {e}")
        add_op_log(
            now, "scrape_all_error", f"批量搜索出错: {row['filename']} - {str(e)}"
        )
        commit()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/files/audio-count")
@require_auth
def api_files_audio_count():
    paths = request.args.get("paths", "").strip()
    if not paths:
        return jsonify({"counts": {}})
    path_list = [p.strip().lstrip("/") for p in paths.split("|") if p.strip()]
    base = Path(MUSIC_ROOT)
    counts = {}

    for rel in path_list:
        rel_normalized = rel.replace("/", "\\") if "\\" in MUSIC_ROOT else rel
        cur = (base / rel_normalized).resolve()
        base_resolved = str(base.resolve())
        cur_str = str(cur)
        if not cur_str.startswith(base_resolved):
            counts[rel] = 0
            continue
        if not cur.is_dir():
            counts[rel] = 0
            continue

        try:
            files = list(cur.rglob("*"))
            audio_files = [
                f
                for f in files
                if f.is_file() and f.suffix.lower().lstrip(".") in ("mp3", "flac")
            ]

            count = len(audio_files)
            counts[rel] = count
        except OSError as e:
            counts[rel] = 0
    return jsonify({"counts": counts})


@api_bp.route("/api/tracks/batch-scrape", methods=["POST"])
@require_auth
def api_batch_scrape():
    data = request.get_json(force=True)
    track_ids = data.get("track_ids", [])
    if not track_ids:
        return jsonify({"ok": False, "error": "缺少 track_ids"}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = get_tracks_by_ids(track_ids)
    row_map = {row["id"]: row for row in rows}

    def scrape_single_track(track_id):
        row = row_map.get(track_id)
        if not row:
            return {
                "track_id": track_id,
                "ok": False,
                "error": "曲目不存在",
                "_log_type": "error",
                "_log_msg": f"批量搜索出错: track_id={track_id} - 曲目不存在",
            }

        current_meta = {
            "title": row["title"],
            "artist": row["artist"],
            "album": row["album"],
            "album_artist": row["album_artist"] if row["album_artist"] else "",
            "track_num": row["track_num"] if row["track_num"] else "",
            "year": row["year"] if row["year"] else "",
        }

        user_input = {}
        user_inputs = data.get("user_inputs", {})
        if isinstance(user_inputs, dict) and str(track_id) in user_inputs:
            track_input = user_inputs[str(track_id)]
            if track_input.get("title") and track_input["title"].strip():
                user_input["title"] = track_input["title"].strip()
            if track_input.get("artist") and track_input["artist"].strip():
                user_input["artist"] = track_input["artist"].strip()
            if track_input.get("album") and track_input["album"].strip():
                user_input["album"] = track_input["album"].strip()
            if track_input.get("track_num") and str(track_input["track_num"]).strip():
                user_input["track_num"] = str(track_input["track_num"]).strip()
            if track_input.get("year") and str(track_input["year"]).strip():
                user_input["year"] = str(track_input["year"]).strip()

        try:
            scrape_results = scraper.search_all_apis(
                row["path"], current_meta, user_input=user_input
            )
            all_items = []
            for api_name, items in scrape_results.items():
                for item in items:
                    item["_api"] = api_name
                    all_items.append(item)

            all_items.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
            best = all_items[0] if all_items else None

            return {
                "track_id": track_id,
                "ok": True,
                "original": current_meta,
                "best": best,
                "all_results": scrape_results,
                "has_cover": bool(row["has_cover"]),
                "track_title": row["title"] if row["title"] else "",
                "track_artist": row["artist"] if row["artist"] else "",
                "track_album": row["album"] if row["album"] else "",
                "filename": row["filename"] if row["filename"] else "",
                "relative_path": get_relative_path(row["path"], MUSIC_ROOT),
                "_log_type": "success",
                "_log_msg": f"批量搜索完成: {row['filename']}",
            }
        except Exception as e:
            return {
                "track_id": track_id,
                "ok": False,
                "error": str(e),
                "track_title": row["title"] if row["title"] else "",
                "filename": row["filename"] if row["filename"] else "",
                "_log_type": "error",
                "_log_msg": f"批量搜索出错: {row['filename']} - {str(e)}",
            }

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scrape_single_track, track_id): track_id
            for track_id in track_ids
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            # log_type = result.get("_log_type")
            # log_msg = result.get("_log_msg")
            # if log_type and log_msg:
            #     add_op_log(now, f"batch_scrape_{log_type}", log_msg)
            result.pop("_log_type", None)
            result.pop("_log_msg", None)

    commit()
    return jsonify({"ok": True, "results": results})


# === 定时任务相关 API ===


@api_bp.route("/api/task/config", methods=["GET"])
@require_auth
def api_get_task_config():
    """获取任务配置"""
    from repository.task_repository import get_task_config

    config = get_task_config()
    return jsonify(
        {
            "scrape_enabled": bool(config["scrape_enabled"]),
            "organize_enabled": bool(config["organize_enabled"]),
            "interval_minutes": config["interval_minutes"],
        }
    )


@api_bp.route("/api/task/config", methods=["POST"])
@require_auth
def api_set_task_config():
    """设置任务配置"""
    from repository.task_repository import set_task_config, commit

    data = request.get_json(force=True)
    scrape_enabled = int(data.get("scrape_enabled", False))
    organize_enabled = int(data.get("organize_enabled", False))
    interval_minutes = int(data.get("interval_minutes", 60))

    # 时间间隔最小为5分钟
    if interval_minutes < 5:
        interval_minutes = 5

    set_task_config(scrape_enabled, organize_enabled, interval_minutes)
    commit()

    # 更新定时任务调度器
    from app import update_scheduler

    update_scheduler()

    return jsonify(
        {
            "ok": True,
            "scrape_enabled": bool(scrape_enabled),
            "organize_enabled": bool(organize_enabled),
            "interval_minutes": interval_minutes,
        }
    )


@api_bp.route("/api/task/status", methods=["GET"])
@require_auth
def api_get_task_status():
    """获取任务状态"""
    from repository.task_repository import get_task_status

    scrape_status = get_task_status("scrape")
    organize_status = get_task_status("organize")
    scheduled_status = get_task_status("scheduled")

    def format_status(status):
        return {
            "status": status["status"],
            "last_run_at": status["last_run_at"],
            "last_success_at": status["last_success_at"],
            "last_failure_at": status["last_failure_at"],
            "next_run_at": status["next_run_at"],
            "error_message": status["error_message"],
            "run_count": status["run_count"],
            "success_count": status["success_count"],
            "failure_count": status["failure_count"],
            "is_manual": bool(status["is_manual"]),
        }

    return jsonify(
        {
            "scrape": format_status(scrape_status),
            "organize": format_status(organize_status),
            "scheduled": format_status(scheduled_status),
        }
    )


@api_bp.route("/api/task/execute", methods=["POST"])
@require_auth
def api_execute_task():
    """手动执行任务"""
    from services.task_service import run_manual_task

    data = request.get_json(force=True)
    task_type = data.get("task_type", "both")

    if task_type not in ["scrape", "organize", "both"]:
        return jsonify({"ok": False, "error": "无效的任务类型"}), 400

    result = run_manual_task(task_type)

    if "error" in result:
        return jsonify({"ok": False, "error": result["error"]}), 500

    return jsonify({"ok": True, "result": result})


@api_bp.route("/api/task/running", methods=["GET"])
@require_auth
def api_get_running_task():
    """检查是否有任务正在运行"""
    from repository.task_repository import is_task_running

    return jsonify(
        {
            "scrape_running": is_task_running("scrape"),
            "organize_running": is_task_running("organize"),
            "scheduled_running": is_task_running("scheduled"),
        }
    )
