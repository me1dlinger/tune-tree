"""
API 路由模块
"""

from flask import Blueprint, request, jsonify, render_template, abort, Response
from functools import wraps
from pathlib import Path
from datetime import datetime
import base64

from config import ACCESS_KEY, MUSIC_ROOT
from utils.metadata import get_cover_b64, get_lyrics
from services.scan_service import scan_library
from services.format_service import preview_format, execute_format
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
    count_total_tracks,
    count_total_artists,
    count_total_albums,
    count_pending_tracks,
    count_organized_artists,
    count_organized_albums,
    count_duplicate_groups,
    count_tracks_by_extension,
    get_scan_meta,
    get_op_logs,
    clear_op_logs,
    commit,
)

api_bp = Blueprint("api", __name__)


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
    if not Path(MUSIC_ROOT).exists():
        return jsonify({"error": f"MUSIC_ROOT '{MUSIC_ROOT}' not found"}), 400
    result = scan_library(MUSIC_ROOT)
    return jsonify(result)


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


# Cover art
@api_bp.route("/api/cover/<int:track_id>")
@require_auth
def api_cover(track_id: int):
    print(track_id)
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
            "Cache-Control": "public, max-age=3600, must-revalidate",
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
    items = []
    for entry in sorted(cur.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        stat = entry.stat()
        items.append(
            {
                "name": entry.name,
                "path": str(entry.relative_to(base)),
                "is_dir": entry.is_dir(),
                "ext": entry.suffix.lower().lstrip(".")
                if not entry.is_dir()
                else "dir",
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
        )
    return jsonify({"path": rel, "items": items})


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
    album_ids = data.get("album_ids", [])
    track_ids = data.get("track_ids", [])
    if track_ids and len(track_ids) > 0:
        result = preview_format(artist, None, track_ids)
    elif artist and album_ids and len(album_ids) > 0:
        result = preview_format(artist, album_ids)
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
    album_ids = data.get("album_ids", [])
    track_ids = data.get("track_ids", [])
    if track_ids and len(track_ids) > 0:
        result = execute_format(artist, None, track_ids)
    elif artist and album_ids and len(album_ids) > 0:
        result = execute_format(artist, album_ids)
    else:
        return jsonify(
            {"error": "artist and album_ids required, or track_ids required"}
        ), 400
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
