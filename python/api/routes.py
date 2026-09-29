"""
API 路由模块
"""

import base64
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import wraps
from pathlib import Path

from config import ACCESS_KEY
from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    request,
    send_file,
    send_from_directory,
)
from repository.album_repository import ensure_album
from repository.album_repository import get_album_stats as repo_get_album_stats
from repository.artist_repository import ensure_artist, get_artist_by_id
from repository.artist_repository import get_artist_stats as repo_get_artist_stats
from repository.library_repository import delete_library as delete_library_repo
from repository.library_repository import (
    get_all_libraries,
    get_current_library,
    get_current_library_id,
    get_current_library_path,
    get_library_by_id,
    insert_library,
    set_current_library_id,
    update_library,
)
from repository.track_repository import (
    add_op_log,
    clear_op_logs,
    commit,
    count_duplicate_groups,
    count_organized_albums,
    count_organized_artists,
    count_pending_tracks,
    count_total_albums,
    count_total_artists,
    count_total_tracks,
    count_tracks_by_extension,
    delete_track_by_id,
    get_artist_directory_path_by_id,
    get_artist_full_info_by_id,
    get_artists,
    get_duplicate_tracks,
    get_op_logs,
    get_pending_tracks,
    get_scan_meta,
    get_scan_status,
    get_track_by_filename,
    get_track_by_filename_and_album,
    get_track_by_filename_and_artist,
    get_track_by_id,
    get_track_by_path,
    get_tracks_by_album_id,
    get_tracks_by_artist_id,
    get_tracks_by_ids,
    insert_track,
    recalc_pending,
    set_scan_finished,
    set_scan_heartbeat,
    set_scan_meta,
    set_scan_running,
    get_scan_heartbeat,
    update_track_by_path,
    update_track_metadata,
)
from services.format_service import (
    batch_execute_format,
    batch_preview_format,
    execute_format,
    preview_format,
)
from services.metadata_scraper import MetadataScraper
from services.netease_api import NeteaseApi
from services.qqmusic_api import QQMusicApi
from services.scan_service import scan_library
from services.similarity_service import find_similar_artists
from utils.formatting import get_relative_path
from utils.metadata import (
    get_cover_b64,
    get_lyrics,
    normalize_str,
    read_metadata,
    write_cover,
    write_lyrics,
    write_metadata,
)

logger = logging.getLogger("tunetree")
api_bp = Blueprint("api", __name__)

SCAN_TIMEOUT_HOURS = 1
scraper = MetadataScraper()

# 全局共享线程池：批量刮削 / 歌词搜索 / 目录音频计数等并发操作复用线程，
# 避免每次请求都新建/销毁线程的开销。
_API_EXECUTOR = ThreadPoolExecutor(max_workers=12, thread_name_prefix="api")

# 扫描后台线程锁：防止并发触发多个扫描任务。
_scan_lock = threading.Lock()
# 当前进行中的扫描取消信号（由 /api/scan 启动、/api/scan/cancel 触发置位）。
_scan_cancel_event: threading.Event | None = None

# 心跳“失联”判定阈值：扫描循环每 20 个文件更新一次心跳；
# 若超过该时长仍无心跳，说明扫描线程可能卡死（如卡在某个文件的 IO 上）。
SCAN_HEARTBEAT_STALE_SECONDS = 90

from services.task_service import get_app as _get_flask_app


def _relink_track_artist_album(track_id: int):
    from repository.album_repository import delete_album, ensure_album, get_album_by_id
    from repository.artist_repository import delete_artist, ensure_artist

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

    new_artist_id = ensure_artist(new_artist_name, library_id=get_current_library_id())
    new_album_id = None
    if new_album_name:
        new_album_id = ensure_album(
            new_album_name,
            new_artist_id,
            year=row["year"],
            library_id=get_current_library_id(),
        )

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
        if not token or not hmac.compare_digest(token, ACCESS_KEY):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


def _is_within(base: Path, target: Path) -> bool:
    """判断 target 是否位于 base 之下（解析为绝对路径，Windows 大小写不敏感）。

    使用 commonpath 做真正的“包含”判断，替代脆弱的 ``startswith``（可被
    ``C:\\music2`` 之类的同名前缀绕过，造成路径穿越读取/写入）。
    """
    try:
        base_r = str(Path(base).resolve())
        tgt_r = str(Path(target).resolve())
    except OSError:
        return False
    if os.name == "nt":
        base_r = base_r.lower()
        tgt_r = tgt_r.lower()
    try:
        return os.path.commonpath([base_r, tgt_r]) == base_r
    except ValueError:
        return False


_MAX_IMAGE_PIXELS = 40_000_000


def _guard_image_dimensions(img) -> bool:
    """防压缩炸弹：检查解码后位图的尺寸/像素总量是否超出安全上限。

    返回 True 表示合法；否则上传方提供的“小体积大尺寸”图片可能在 PIL
    解码时耗尽服务器内存与 CPU。
    """
    try:
        if img.width > 8192 or img.height > 8192:
            return False
        if (img.width * img.height) > _MAX_IMAGE_PIXELS:
            return False
        return True
    except Exception:
        return False


@api_bp.route("/")
def index():
    return send_from_directory("static", "index.html")


# Auth
@api_bp.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    data = request.get_json(force=True)
    token = data.get("token", "")
    if token and hmac.compare_digest(token, ACCESS_KEY):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid key"}), 401


# Scan
@api_bp.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    global _scan_cancel_event
    _check_scan_timeout()

    scan_status = get_scan_status()
    if scan_status["scanning"]:
        return jsonify(
            {"error": "scan_in_progress", "message": "扫描正在进行中，请稍后"}
        ), 409

    music_root = get_current_library_path()
    if not music_root or not Path(music_root).exists():
        return jsonify({"error": "Music library path not found"}), 400

    if not _scan_lock.acquire(blocking=False):
        return jsonify(
            {"error": "scan_in_progress", "message": "扫描正在进行中，请稍后"}
        ), 409

    library_id = get_current_library_id()

    # 创建本轮扫描的取消信号：/api/scan/cancel 置位后，扫描循环协作式退出
    cancel_event = threading.Event()
    _scan_cancel_event = cancel_event

    # 先标记「扫描中」，保证 /api/scan 返回后前端立刻能轮询到 scanning=true。
    # 否则后台线程真正启动前，轮询可能读到上一轮的 idle 状态而误判扫描已结束（竞态）。
    set_scan_running(datetime.now().timestamp())
    # 清空上一轮结果，避免本轮尚未产出结果时前端读到旧数据
    try:
        set_scan_meta("last_scan_result", "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("清空上次扫描结果失败: %s", exc)

    def _do_scan():
        """后台实际执行扫描（需要在 Flask 应用上下文中访问数据库）"""
        try:
            result = scan_library(
                music_root, library_id=library_id, cancel_event=cancel_event
            )
            # 保存本次扫描结果，供前端在轮询/完成后展示
            try:
                set_scan_meta(
                    "last_scan_result",
                    json.dumps(
                        {
                            k: result.get(k)
                            for k in (
                                "added",
                                "updated",
                                "skipped",
                                "removed",
                                "duration",
                                "cancelled",
                            )
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as log_exc:  # noqa: BLE001
                logger.warning("保存扫描结果失败: %s", log_exc)
            logger.info("后台扫描完成: %s", result)
        finally:
            set_scan_finished()

    def _scan_worker():
        """后台线程入口：在应用上下文里跑扫描，结束后释放锁"""
        try:
            flask_app = _get_flask_app()
            if flask_app is not None:
                with flask_app.app_context():
                    _do_scan()
            else:
                _do_scan()
        except Exception as exc:  # noqa: BLE001
            logger.error("后台扫描失败: %s", exc)
        finally:
            _scan_lock.release()

    # 后台线程执行，接口立即返回，防止长时间占用请求线程导致整个服务卡顿
    threading.Thread(target=_scan_worker, name="library-scan", daemon=True).start()

    return jsonify({"started": True, "status": "running"})


@api_bp.route("/api/scan/status", methods=["GET"])
@require_auth
def api_scan_status():
    timed_out = _check_scan_timeout()
    scan_status = get_scan_status()

    last_result = None
    try:
        raw = get_scan_meta("last_scan_result")
        if raw:
            last_result = json.loads(raw)
    except Exception:  # noqa: BLE001
        last_result = None

    heartbeat_age = None
    if scan_status["scanning"]:
        hb = get_scan_heartbeat()
        if hb:
            heartbeat_age = max(0, int(datetime.now().timestamp() - hb))

    if scan_status["scanning"] and scan_status["start_time"]:
        elapsed = int(datetime.now().timestamp() - scan_status["start_time"])
        return jsonify(
            {
                "scanning": True,
                "elapsed_seconds": elapsed,
                "timed_out": False,
                "heartbeat_age_seconds": heartbeat_age,
                "last_result": last_result,
            }
        )
    return jsonify(
        {
            "scanning": False,
            "elapsed_seconds": 0,
            "timed_out": timed_out,
            "heartbeat_age_seconds": None,
            "last_result": last_result,
        }
    )


@api_bp.route("/api/scan/cancel", methods=["POST"])
@require_auth
def api_scan_cancel():
    """请求停止当前扫描（协作式取消）。

    置位取消事件后，扫描循环会在下一个检查点退出并尽快返回；
    前端通过轮询 /api/scan/status 等待 scanning 变为 false。
    """
    global _scan_cancel_event

    scan_status = get_scan_status()
    if not scan_status["scanning"]:
        return jsonify({"error": "not_scanning", "message": "当前没有进行中的扫描"}), 400

    ev = _scan_cancel_event
    if ev is None:
        # 数据库标记在扫描，但本进程没有对应的扫描线程（例如另一 worker 在跑，
        # 或状态残留）。此时无法在本进程取消，提示用户稍后重试/重启即自动复位。
        logger.warning("收到取消请求，但本进程没有可取消的扫描事件，可能由其他进程执行")
        return (
            jsonify(
                {"error": "not_cancellable", "message": "扫描由其他进程执行，无法在此取消"}
            ),
            409,
        )

    ev.set()
    logger.info("用户请求取消扫描")
    return jsonify({"ok": True, "message": "已请求停止扫描"})


# Artists
@api_bp.route("/api/artists")
@require_auth
def api_artists():
    q = request.args.get("q", "").strip()
    library_id = get_current_library_id()
    rows = get_artists(q, library_id=library_id)
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

    base = Path(get_current_library_path() or "")
    file_path = (base / path).resolve()
    if not _is_within(base, file_path):
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


from models.db import get_db
from repository.album_repository import get_album_by_id, get_albums_by_artist_id
from repository.album_repository import update_album as update_album_repo

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
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_data))
        if not _guard_image_dimensions(img):
            return jsonify({"error": "cover image too large"}), 400
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
        from repository.artist_repository import update_artist

        update_artist(artist_id, cover_path="")
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

    album_dir = (
        Path(get_current_library_path() or "")
        / artist_row["dir_name"]
        / album["dir_name"]
    )
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
        Path(get_current_library_path() or "")
        / artist_row["dir_name"]
        / album["dir_name"]
        / ALBUM_COVER_FILENAME
    )
    return jsonify({"exists": cover_path.exists()})


def _artist_cover_exists(artist_id: int) -> bool:
    """判断艺术家封面文件是否存在（与单条 cover/exists 逻辑一致）"""
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        return False
    return (Path(artist_dir) / ARTIST_COVER_FILENAME).exists()


def _album_cover_exists(album_id: int) -> bool:
    """判断专辑封面文件是否存在（与单条 cover/exists 逻辑一致）"""
    album = get_album_by_id(album_id)
    if not album:
        return False
    artist_row = get_artist_by_id(album["artist_id"]) if album["artist_id"] else None
    if not artist_row:
        return False
    cover_path = (
        Path(get_current_library_path() or "")
        / artist_row["dir_name"]
        / album["dir_name"]
        / ALBUM_COVER_FILENAME
    )
    return cover_path.exists()


@api_bp.route("/api/covers/batch", methods=["POST"])
@require_auth
def api_covers_batch():
    """批量查询艺术家/专辑封面是否存在。

    前端在渲染艺术家视图时一次性提交当前页所有专辑 ID，
    将原来的 N 次 cover/exists 请求合并为 1 次，显著减少前后端往返。
    """
    data = request.get_json(force=True) or {}
    albums = {}
    for album_id in data.get("album_ids", []) or []:
        try:
            albums[str(album_id)] = _album_cover_exists(int(album_id))
        except (TypeError, ValueError):
            albums[str(album_id)] = False

    artists = {}
    for artist_id in data.get("artist_ids", []) or []:
        try:
            artists[str(artist_id)] = _artist_cover_exists(int(artist_id))
        except (TypeError, ValueError):
            artists[str(artist_id)] = False

    return jsonify({"albums": albums, "artists": artists})


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
        import io as _io

        from PIL import Image

        img = Image.open(_io.BytesIO(image_data))
        if not _guard_image_dimensions(img):
            return jsonify({"error": "cover image too large"}), 400
        if img.format != "JPEG":
            img = img.convert("RGB")

        album_dir = (
            Path(get_current_library_path() or "")
            / artist_row["dir_name"]
            / album["dir_name"]
        )
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
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_data))
        if not _guard_image_dimensions(img):
            return jsonify({"error": "cover image too large"}), 400
        if img.format != "JPEG":
            img = img.convert("RGB")

        cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
        img.save(cover_path, "JPEG", quality=90)
        if not artist_row["cover_path"]:
            from repository.artist_repository import update_artist

            update_artist(artist_id, cover_path=str(cover_path))

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


@api_bp.route("/api/artists/search-avatars", methods=["POST"])
@require_auth
def api_artists_search_avatars():
    data = request.get_json(force=True) or {}
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400

    netease_results = []
    qq_results = []

    try:
        raw = NeteaseApi.search_artist(keyword, limit=5)
        for r in raw:
            pic_url = r.get("picUrl", "")
            if pic_url:
                pic_url = pic_url.replace("http://", "https://")
                if "?" not in pic_url:
                    pic_url += "?param=300y300"
            netease_results.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name", ""),
                    "picUrl": pic_url,
                }
            )
    except Exception as e:
        logger.warning("网易云搜索歌手头像失败: %s", e)

    try:
        raw = QQMusicApi.search_artist(keyword, limit=5)
        for r in raw:
            qq_results.append(
                {
                    "id": r.get("id"),
                    "mid": r.get("mid", ""),
                    "name": r.get("name", ""),
                    "picUrl": r.get("picUrl", ""),
                }
            )
    except Exception as e:
        logger.warning("QQ音乐搜索歌手头像失败: %s", e)

    return jsonify({"netease": netease_results, "qq": qq_results})


@api_bp.route("/api/artists/<int:artist_id>/apply-avatar", methods=["POST"])
@require_auth
def api_artists_apply_avatar(artist_id: int):
    data = request.get_json(force=True) or {}
    pic_url = data.get("picUrl", "").strip()
    if not pic_url:
        return jsonify({"error": "picUrl is required"}), 400

    artist_row = get_artist_by_id(artist_id)
    if not artist_row:
        abort(404)
    artist_dir = get_artist_directory_path_by_id(artist_id)
    if not artist_dir:
        return jsonify({"error": "artist directory not found"}), 404

    # SSRF 防护：只允许拉取已知图片 CDN 域名下的 http(s) 图片，
    # 并拒绝内网/回环等私有地址，防止服务器被当作跳板访问内部服务。
    import socket
    import urllib.parse as _up

    _ALLOWED_AVATAR_HOSTS = (
        "music.163.com",
        "music.126.net",
        "y.gtimg.cn",
        "qqmusic.qq.com",
    )

    def _is_allowed_avatar_url(url: str) -> bool:
        try:
            parsed = _up.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            host = (parsed.hostname or "").lower()
            if not any(
                host == h or host.endswith("." + h) for h in _ALLOWED_AVATAR_HOSTS
            ):
                return False
            addr = socket.getaddrinfo(host, None)[0][4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
            return True
        except Exception:
            return False

    if not _is_allowed_avatar_url(pic_url):
        return jsonify({"error": "picUrl 域名不在允许列表内"}), 400

    try:
        import requests as _req

        if "y.gtimg.cn" in pic_url:
            resp = _req.get(pic_url, headers=QQMusicApi._HEADERS, timeout=15)
        else:
            from services.netease_api import APIConstants

            resp = _req.get(
                pic_url,
                headers={
                    "User-Agent": APIConstants.USER_AGENT,
                    "Referer": APIConstants.REFERER,
                },
                timeout=15,
            )
        resp.raise_for_status()
        image_data = resp.content
    except Exception as e:
        logger.error("下载头像失败 %s: %s", pic_url, e)
        return jsonify({"error": f"failed to download avatar: {e}"}), 502

    if not image_data or len(image_data) < 1000:
        return jsonify({"error": "downloaded image too small or empty"}), 502

    if len(image_data) > MAX_ARTIST_COVER_SIZE:
        return jsonify({"error": "cover file too large (max 5MB)"}), 400

    try:
        import io as _io

        from PIL import Image

        img = Image.open(_io.BytesIO(image_data))
        # 防压缩炸弹：拒绝超大尺寸图片（避免解码时耗尽内存/CPU）
        if (
            img.width > 8192
            or img.height > 8192
            or (img.width * img.height) > 40_000_000
        ):
            return jsonify({"error": "图片尺寸过大"}), 400
        if img.format != "JPEG":
            img = img.convert("RGB")

        cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
        img.save(cover_path, "JPEG", quality=90)
        if not artist_row["cover_path"]:
            from repository.artist_repository import update_artist

            update_artist(artist_id, cover_path=str(cover_path))
    except Exception as exc:
        logger.error("artist avatar save error %d: %s", artist_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "path": str(cover_path)})


# Lyrics search and fetch
@api_bp.route("/api/lyrics/search", methods=["POST"])
@require_auth
def api_lyrics_search():
    data = request.get_json(force=True) or {}
    title = data.get("title", "")
    artist = data.get("artist", "")
    album = data.get("album", "")

    keyword_parts = [p for p in [title, artist, album] if p]
    keyword = "|".join(keyword_parts)
    if not keyword:
        return jsonify({"error": "at least one of title/artist/album is required"}), 400

    def _score_result(r, ref_title, ref_artist, ref_album):
        from services.metadata_scraper import normalize_str

        score = 0.0
        if ref_title:
            rn = normalize_str(r.get("songName", ""))
            tn = normalize_str(ref_title)
            if rn == tn:
                score += 10
            elif tn in rn or rn in tn:
                score += 5
        if ref_artist:
            rn = normalize_str(r.get("singer", ""))
            an = normalize_str(ref_artist)
            if rn == an:
                score += 8
            elif an in rn or rn in an:
                score += 4
        if ref_album:
            rn = normalize_str(r.get("album", ""))
            bn = normalize_str(ref_album)
            if rn == bn:
                score += 6
            elif bn in rn or rn in bn:
                score += 3
        return score

    try:
        all_results = []

        def _search_netease():
            try:
                return NeteaseApi.search_song(keyword)
            except Exception:
                return []

        def _search_qq():
            try:
                return QQMusicApi.search_song(keyword)
            except Exception:
                return []

        netease_future = _API_EXECUTOR.submit(_search_netease)
        qq_future = _API_EXECUTOR.submit(_search_qq)
        netease_raw = netease_future.result()
        qq_raw = qq_future.result()

        for r in netease_raw:
            score = _score_result(r, title, artist, album)
            all_results.append(
                {
                    "id": r["idOrMd5"],
                    "title": r["songName"],
                    "artist": r["singer"],
                    "album": r.get("album", ""),
                    "duration": r["duration"],
                    "source": "netease",
                    "_score": score,
                }
            )

        for r in qq_raw:
            score = _score_result(r, title, artist, album)
            all_results.append(
                {
                    "id": r["idOrMd5"],
                    "title": r["songName"],
                    "artist": r["singer"],
                    "album": r.get("album", ""),
                    "duration": r["duration"],
                    "source": "qq",
                    "_song_id": r.get("_song_id", ""),
                    "_song_mid": r.get("_song_mid", ""),
                    "_score": score,
                }
            )

        all_results.sort(key=lambda x: x["_score"], reverse=True)
        all_results = all_results[:10]

        return jsonify({"ok": True, "results": all_results})
    except Exception as e:
        logger.error(f"搜索歌词失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/lyrics/<song_id>", methods=["GET"])
@require_auth
def api_lyrics_fetch(song_id: str):
    source = request.args.get("source", "netease")
    try:
        if source == "qq":
            song_mid = request.args.get("song_mid", song_id)
            song_id_int = int(request.args.get("song_id", "0") or "0")
            song_name = request.args.get("song_name", "")
            singers_str = request.args.get("singers", "")
            singers = (
                [s.strip() for s in singers_str.split(",") if s.strip()]
                if singers_str
                else []
            )
            album_name = request.args.get("album_name", "")
            duration = int(request.args.get("duration", "0") or "0")
            lyrics = QQMusicApi.get_lyrics_by_song_mid(
                song_mid, song_id_int, song_name, singers, album_name, duration
            )
        else:
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
    d["relative_path"] = get_relative_path(
        row["path"], get_current_library_path() or ""
    )
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
    _lib_path = get_current_library_path() or ""
    rel_path_normalized = rel_path.replace("/", "\\") if "\\" in _lib_path else rel_path
    full_path = str(Path(_lib_path) / rel_path.lstrip("/"))
    full_path_normalized = str(Path(_lib_path) / rel_path_normalized.lstrip("/"))

    # 目录穿越防护：只允许音乐库内的相对路径（拒绝 ../ 等越界路径）
    if _lib_path:
        _base = Path(_lib_path).resolve()
        if not any(
            _is_within(_base, Path(p)) for p in (full_path, full_path_normalized)
        ):
            return jsonify({"error": "path outside library"}), 403

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
        if candidate and Path(candidate).suffix.lower() in (".mp3", ".flac", ".m4a"):
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
                    from repository.album_repository import ensure_album
                    from repository.artist_repository import ensure_artist

                    effective_artist = album_artist_name or artist_name
                    artist_id = ensure_artist(
                        effective_artist, library_id=get_current_library_id()
                    )
                    if album_name:
                        album_id = ensure_album(
                            album_name,
                            artist_id,
                            year=meta.get("year"),
                            library_id=get_current_library_id(),
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
                    library_id=get_current_library_id(),
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
    base = Path(get_current_library_path() or "")
    cur = (base / rel).resolve()
    if not _is_within(base, cur):
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
                    if ".upload_temp" in entry.relative_to(base).parts:
                        continue
                    ext = entry.suffix.lower().lstrip(".")
                    if ext not in ("mp3", "flac", "m4a"):
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
            if entry.name == ".upload_temp" and cur == base:
                continue
            stat = entry.stat()
            is_dir = entry.is_dir()
            ext = entry.suffix.lower().lstrip(".") if not is_dir else "dir"
            is_audio = ext in ("mp3", "flac", "m4a") if not is_dir else False
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
    library_id = get_current_library_id()
    total_tracks = count_total_tracks(library_id=library_id)
    total_artists = count_total_artists(library_id=library_id)
    total_albums = count_total_albums(library_id=library_id)
    pending_count = count_pending_tracks(library_id=library_id)
    org_artists = count_organized_artists(library_id=library_id)
    org_albums = count_organized_albums(library_id=library_id)
    dupes = count_duplicate_groups(library_id=library_id)
    flac_count = count_tracks_by_extension(".flac", library_id=library_id)
    mp3_count = count_tracks_by_extension(".mp3", library_id=library_id)
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


@api_bp.route("/api/stats/artists")
@require_auth
def api_artist_stats():
    library_id = get_current_library_id()
    stats = repo_get_artist_stats(library_id=library_id)
    return jsonify(stats)


@api_bp.route("/api/stats/albums")
@require_auth
def api_album_stats():
    library_id = get_current_library_id()
    stats = repo_get_album_stats(library_id=library_id)
    return jsonify(stats)


@api_bp.route("/api/stats/similar-artists")
@require_auth
def api_similar_artists():
    library_id = get_current_library_id()
    groups = find_similar_artists(library_id=library_id)
    from repository.album_repository import get_albums_by_artist_id
    from repository.track_repository import get_tracks_by_artist_id

    result = []
    for g in groups:
        aid = g["artist_a"]["id"]
        bid = g["artist_b"]["id"]
        a_albums = get_albums_by_artist_id(aid)
        b_albums = get_albums_by_artist_id(bid)
        a_tracks = get_tracks_by_artist_id(aid)
        b_tracks = get_tracks_by_artist_id(bid)
        result.append(
            {
                "artist_a": {
                    **g["artist_a"],
                    "album_count": len(a_albums),
                    "track_count": len(a_tracks),
                },
                "artist_b": {
                    **g["artist_b"],
                    "album_count": len(b_albums),
                    "track_count": len(b_tracks),
                },
                "similarity": g["similarity"],
            }
        )
    return jsonify(result)


@api_bp.route("/api/stats/similar-artists/<int:artist_a_id>/<int:artist_b_id>")
@require_auth
def api_similar_artists_detail(artist_a_id, artist_b_id):
    from repository.album_repository import get_albums_by_artist_id
    from repository.track_repository import get_tracks_by_artist_id

    a = get_artist_by_id(artist_a_id)
    b = get_artist_by_id(artist_b_id)
    if not a or not b:
        abort(404)

    a_albums = [dict(r) for r in get_albums_by_artist_id(artist_a_id)]
    b_albums = [dict(r) for r in get_albums_by_artist_id(artist_b_id)]
    a_tracks = [dict(r) for r in get_tracks_by_artist_id(artist_a_id)]
    b_tracks = [dict(r) for r in get_tracks_by_artist_id(artist_b_id)]

    return jsonify(
        {
            "artist_a": {
                "id": a["id"],
                "name": a["name"],
                "albums": a_albums,
                "tracks": a_tracks,
            },
            "artist_b": {
                "id": b["id"],
                "name": b["name"],
                "albums": b_albums,
                "tracks": b_tracks,
            },
        }
    )


@api_bp.route("/api/artists/batch-scrape-covers", methods=["POST"])
@require_auth
def api_batch_scrape_covers():
    data = request.get_json(force=True) or {}
    artist_ids = data.get("artist_ids", [])
    if not artist_ids:
        return jsonify({"error": "artist_ids required"}), 400

    results = []
    for aid in artist_ids:
        artist_row = get_artist_by_id(aid)
        if not artist_row:
            results.append({"id": aid, "ok": False, "error": "not found"})
            continue
        artist_name = artist_row["name"]
        artist_dir = get_artist_directory_path_by_id(aid)
        if not artist_dir:
            results.append({"id": aid, "ok": False, "error": "no directory"})
            continue
        try:
            image_data, successful_artist = scraper.scrape_artist_avatar(artist_name)
            if not image_data or len(image_data) < 1000:
                results.append({"id": aid, "ok": False, "error": "no image"})
                continue
            if len(image_data) > MAX_ARTIST_COVER_SIZE:
                results.append({"id": aid, "ok": False, "error": "too large"})
                continue
            import io as _io

            from PIL import Image

            img = Image.open(_io.BytesIO(image_data))
            if not _guard_image_dimensions(img):
                results.append({"id": aid, "ok": False, "error": "image too large"})
                continue
            if img.format != "JPEG":
                img = img.convert("RGB")
            cover_path = Path(artist_dir) / ARTIST_COVER_FILENAME
            img.save(cover_path, "JPEG", quality=90)
            from repository.artist_repository import update_artist

            update_artist(aid, cover_path=str(cover_path))
            results.append({"id": aid, "ok": True, "name": artist_name})
        except Exception as exc:
            logger.error("batch scrape cover error %d: %s", aid, exc)
            results.append({"id": aid, "ok": False, "error": str(exc)})

    ok_count = sum(1 for r in results if r["ok"])
    return jsonify({"results": results, "total": len(results), "success": ok_count})


# Pending files
@api_bp.route("/api/pending")
@require_auth
def api_pending():
    library_id = get_current_library_id()
    rows = get_pending_tracks(library_id=library_id)
    result = []
    for row in rows:
        d = dict(row)
        d["relative_path"] = get_relative_path(
            row["path"], get_current_library_path() or ""
        )
        result.append(d)
    return jsonify(result)


# Duplicates
@api_bp.route("/api/duplicates")
@require_auth
def api_duplicates():
    library_id = get_current_library_id()
    rows = get_duplicate_tracks(library_id=library_id)
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
    rows = get_op_logs(200, library_id=get_current_library_id())
    return jsonify([dict(r) for r in rows])


@api_bp.route("/api/logs", methods=["DELETE"])
@require_auth
def api_logs_clear():
    clear_op_logs(library_id=get_current_library_id())
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
                library_id=get_current_library_id(),
            )
            commit()
            return jsonify(
                {"ok": True, "original": current_meta, "scraped": scraped_data}
            )
        else:
            add_op_log(
                now,
                "scrape_fail",
                f"未能找到匹配的元数据: {row['filename']}",
                library_id=get_current_library_id(),
            )
            commit()
            return jsonify({"ok": False, "error": "未找到匹配的元数据"})
    except Exception as e:
        logger.error(f"刮削元数据失败: {e}")
        add_op_log(
            now,
            "scrape_error",
            f"刮削元数据出错: {row['filename']} - {e!s}",
            library_id=get_current_library_id(),
        )
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
                            get_current_library_path() or "",
                            artist_row["dir_name"],
                            album["dir_name"],
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
        add_op_log(
            now,
            "apply_scrape_success",
            f"成功应用元数据: {row['filename']}",
            library_id=get_current_library_id(),
        )
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
            now,
            "apply_scrape_error",
            f"应用元数据出错: {row['filename']} - {e!s}",
            library_id=get_current_library_id(),
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
            now,
            "scrape_all_error",
            f"批量搜索出错: {row['filename']} - {e!s}",
            library_id=get_current_library_id(),
        )
        commit()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bp.route("/api/tracks/<int:track_id>/scrape-source", methods=["POST"])
@require_auth
def api_scrape_source(track_id: int):
    """只搜索指定的音乐源，返回该源的候选标签列表（供用户手动挑选）。"""
    row = get_track_by_id(track_id)
    if not row:
        abort(404)

    data = request.get_json(silent=True) or {}
    source = data.get("source", "")
    if source not in ("cloud", "qq", "kugou"):
        return jsonify({"ok": False, "error": "无效的音乐源"}), 400

    current_meta = {
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "track_num": row["track_num"] if row["track_num"] else "",
        "year": row["year"] if row["year"] else "",
        "filename": row["filename"] if row["filename"] else "",
    }

    exclude_ids = data.get("exclude_ids", []) or []

    user_input = {}
    for key in ("title", "artist", "album", "track_num", "year"):
        val = data.get(key)
        if val is not None and str(val).strip():
            user_input[key] = str(val).strip()

    try:
        results = scraper.search_one_api(
            row["path"], current_meta, source, exclude_ids, user_input
        )
        commit()
        return jsonify({"ok": True, "source": source, "results": results})
    except Exception as e:
        logger.error(f"单源搜索失败: {e}")
        commit()
        return jsonify({"ok": False, "error": str(e)}), 500


ALLOWED_UPLOAD_EXTS = {"flac", "mp3", "m4a"}
MAX_UPLOAD_FILE_SIZE = 500 * 1024 * 1024  # 单个上传文件上限 500MB


def _get_upload_temp_dir():
    return Path(get_current_library_path() or "") / ".upload_temp"


def _safe_upload_filename(name: str) -> str:
    """清理上传文件名，防止目录穿越写入。

    去除路径分隔符、控制字符以及 ``..``/纯点号，确保最终落盘文件名
    只会是临时目录/目标目录下的普通文件名。
    """
    if not name:
        return "upload"
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = name.strip()
    if re.fullmatch(r"\.+", name):
        name = "_"
    name = name.replace("..", "_")
    name = name.strip(" .")
    return name or "upload"


def _valid_temp_id(temp_id: str) -> bool:
    """校验 temp_id 是否是一个合法的临时文件名（纯 basename，不可含路径/穿越）。"""
    if not temp_id or temp_id in (".", ".."):
        return False
    if "/" in temp_id or "\\" in temp_id:
        return False
    if Path(temp_id).name != temp_id:
        return False
    return True


def _find_matching_track(artist: str | None, album: str | None, title: str | None):
    """Find a track in DB matching artist + album + title (all normalized).

    Uses broad SQL query (matching on raw or normalized title) then
    Python-side normalize_str() verification for reliable Unicode matching.
    """
    if not title:
        return None
    db = get_db()
    norm_title = normalize_str(title)
    norm_artist = normalize_str(artist) if artist else ""
    norm_album = normalize_str(album) if album else ""
    params: list[str] = [title, norm_title]
    where_parts = ["(t.title = ? OR t.title = ?)"]
    if norm_artist:
        where_parts.append("t.artist = ?")
        params.append(artist or "")
    if album:
        where_parts.append("t.album = ?")
        params.append(album or "")
    rows = db.execute(
        "SELECT t.*, ar.name as artist_name FROM tracks t "
        "LEFT JOIN artists ar ON t.artist_id = ar.id "
        "WHERE " + " AND ".join(where_parts),
        tuple(params),
    ).fetchall()
    for row in rows:
        if normalize_str(row["title"] or "") != norm_title:
            continue
        if norm_artist and normalize_str(row["artist"] or "") != norm_artist:
            continue
        if norm_album and normalize_str(row["album"] or "") != norm_album:
            continue
        return row
    return None


@api_bp.route("/api/files/upload-check", methods=["POST"])
@require_auth
def api_files_upload_check():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "未选择文件"}), 400

    _get_upload_temp_dir().mkdir(parents=True, exist_ok=True)

    conflicts = []
    new_files = []
    errors = []

    for f in files:
        filename = f.filename
        if not filename:
            errors.append({"name": "", "error": "文件名为空"})
            continue

        ext = Path(filename).suffix.lower().lstrip(".")
        if ext not in ALLOWED_UPLOAD_EXTS:
            errors.append(
                {"name": filename, "error": f"不支持的格式 .{ext}，仅支持 FLAC/MP3/M4A"}
            )
            continue

        if f.content_length and f.content_length > MAX_UPLOAD_FILE_SIZE:
            errors.append(
                {
                    "name": filename,
                    "error": f"文件过大（超过 {MAX_UPLOAD_FILE_SIZE // (1024 * 1024)}MB）",
                }
            )
            continue

        temp_id = f"{int(time.time() * 1000)}_{_safe_upload_filename(filename)}"
        temp_path = _get_upload_temp_dir() / temp_id

        try:
            f.save(str(temp_path))
        except OSError as e:
            errors.append({"name": filename, "error": str(e)})
            continue

        try:
            meta = read_metadata(str(temp_path))
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            errors.append({"name": filename, "error": f"读取元数据失败: {e}"})
            continue

        artist = normalize_str(meta.get("artist") or "")
        album = meta.get("album") or ""
        title = meta.get("title") or ""

        existing = _find_matching_track(artist or None, album or None, title or None)

        file_info = {
            "temp_id": temp_id,
            "name": filename,
            "size": temp_path.stat().st_size,
            "title": title or filename,
            "artist": artist,
            "album": album,
        }

        if existing:
            rel_path = (
                str(
                    Path(existing["path"]).relative_to(
                        Path(get_current_library_path() or "")
                    )
                )
                if existing["path"]
                else ""
            )
            file_info["existing"] = {
                "id": existing["id"],
                "path": existing["path"],
                "rel_dir": str(Path(rel_path).parent) if rel_path else "",
                "filename": Path(existing["path"]).name if existing["path"] else "",
                "title": existing["title"],
                "artist": existing["artist_name"] or existing["artist"],
                "album": existing["album"],
            }
            conflicts.append(file_info)
        else:
            new_files.append(file_info)

    return jsonify(
        {
            "ok": True,
            "conflicts": conflicts,
            "new_files": new_files,
            "errors": errors,
        }
    )


@api_bp.route("/api/files/upload-commit", methods=["POST"])
@require_auth
def api_files_upload_commit():
    data = request.get_json(force=True)
    target = data.get("path", "").lstrip("/")
    resolve = data.get("resolve", {})

    base = Path(get_current_library_path() or "")
    cur = (base / target).resolve()
    if not _is_within(base, cur):
        abort(403)
    if not cur.is_dir():
        abort(400)

    uploaded = []
    skipped = []
    errors = []

    for temp_id, action in resolve.items():
        if not _valid_temp_id(temp_id):
            errors.append({"name": temp_id, "error": "非法临时文件ID"})
            continue
        temp_path = _get_upload_temp_dir() / temp_id
        if not temp_path.exists():
            errors.append({"name": temp_id, "error": "临时文件不存在"})
            continue

        if action == "skip":
            temp_path.unlink(missing_ok=True)
            skipped.append(
                {"name": temp_id.split("_", 1)[-1] if "_" in temp_id else temp_id}
            )
            continue

        try:
            if action == "overwrite":
                existing_id = data.get("overwrite_ids", {}).get(temp_id)
                if not existing_id:
                    temp_path.unlink(missing_ok=True)
                    errors.append({"name": temp_id, "error": "缺少覆盖目标ID"})
                    continue

                existing_row = get_track_by_id(existing_id)
                if not existing_row:
                    temp_path.unlink(missing_ok=True)
                    errors.append({"name": temp_id, "error": "目标曲目不存在"})
                    continue

                existing_path = Path(existing_row["path"])
                if not _is_within(base, existing_path):
                    temp_path.unlink(missing_ok=True)
                    errors.append({"name": temp_id, "error": "覆盖目标路径非法"})
                    continue
                dest = existing_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                shutil.move(str(temp_path), str(dest))

                _ingest_uploaded_file(dest, existing_row["id"])
                uploaded.append(
                    {
                        "name": dest.name,
                        "size": dest.stat().st_size,
                        "path": str(dest.relative_to(base)),
                    }
                )
            else:
                dest = cur / (temp_id.split("_", 1)[-1] if "_" in temp_id else temp_id)
                if not _is_within(cur, dest) or not _is_within(base, dest):
                    temp_path.unlink(missing_ok=True)
                    errors.append({"name": temp_id, "error": "目标路径非法"})
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)

                shutil.move(str(temp_path), str(dest))

                _ingest_uploaded_file(dest)
                uploaded.append(
                    {
                        "name": dest.name,
                        "size": dest.stat().st_size,
                        "path": str(dest.relative_to(base)),
                    }
                )
        except Exception as e:
            logger.error(f"上传提交失败 {temp_id}: {e}")
            temp_path.unlink(missing_ok=True)
            errors.append({"name": temp_id, "error": str(e)})

    for leftover in _get_upload_temp_dir().iterdir():
        try:
            if leftover.is_file():
                age = time.time() - leftover.stat().st_mtime
                if age > 3600:
                    leftover.unlink()
        except OSError:
            pass

    return jsonify(
        {
            "ok": True,
            "uploaded": uploaded,
            "skipped": skipped,
            "errors": errors,
        }
    )


def _ingest_uploaded_file(dest: Path, existing_track_id: int | None = None):
    """Read metadata from an uploaded file and insert/update the tracks table + link artist/album."""

    path_str = str(dest)
    stat = dest.stat()
    mtime = stat.st_mtime
    ctime = stat.st_ctime
    size = stat.st_size
    ext = dest.suffix.lower().lstrip(".")
    filename = dest.name
    scanned_at = time.time()

    meta = read_metadata(path_str)
    missing = [f for f in ("title", "artist", "album") if not meta.get(f)]
    pending = 1 if missing else 0
    missing_str = ",".join(missing) if missing else ""

    artist_name = normalize_str(meta.get("artist") or "")
    album_name = meta.get("album") or ""
    album_artist_name = meta.get("album_artist") or ""
    track_artist_name = artist_name
    year = meta.get("year")

    effective_artist = album_artist_name or artist_name
    artist_id = None
    album_id = None
    if effective_artist:
        artist_id = ensure_artist(effective_artist, library_id=get_current_library_id())
        if album_name:
            album_id = ensure_album(
                album_name, artist_id, year=year, library_id=get_current_library_id()
            )

    if existing_track_id:
        update_track_metadata(
            existing_track_id,
            {
                "title": meta["title"],
                "artist": artist_name or None,
                "album": album_name,
                "album_artist": meta["album_artist"],
                "year": year,
                "track_num": meta["track_num"],
                "disc_num": meta["disc_num"],
                "duration": meta["duration"],
                "sample_rate": meta["sample_rate"],
                "bitrate": meta["bitrate"],
                "has_cover": meta["has_cover"],
                "has_lyrics": meta["has_lyrics"],
                "artist_id": artist_id,
                "album_id": album_id,
                "track_artist": track_artist_name,
            },
        )
        db = get_db()
        db.execute(
            "UPDATE tracks SET filename=?, ext=?, size=?, mtime=?, ctime=?, pending=?, missing_tags=?, scanned_at=? WHERE id=?",
            (
                filename,
                ext,
                size,
                mtime,
                ctime,
                pending,
                missing_str,
                scanned_at,
                existing_track_id,
            ),
        )
        db.commit()
    else:
        existing = get_track_by_path(path_str)
        if existing:
            update_track_by_path(
                filename,
                ext,
                size,
                mtime,
                ctime,
                meta["title"],
                artist_name or None,
                album_name,
                meta["album_artist"],
                year,
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
                path_str,
                artist_id,
                album_id,
                track_artist_name,
                library_id=get_current_library_id(),
            )
        else:
            insert_track(
                path_str,
                filename,
                ext,
                size,
                mtime,
                ctime,
                meta["title"],
                artist_name or None,
                album_name,
                meta["album_artist"],
                year,
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
                artist_id,
                album_id,
                track_artist_name,
                library_id=get_current_library_id(),
            )


@api_bp.route("/api/files/upload-cancel", methods=["POST"])
@require_auth
def api_files_upload_cancel():
    data = request.get_json(force=True)
    temp_ids = data.get("temp_ids", [])
    removed = 0
    for temp_id in temp_ids:
        if not _valid_temp_id(temp_id):
            continue
        temp_path = _get_upload_temp_dir() / temp_id
        if temp_path.exists():
            try:
                temp_path.unlink()
                removed += 1
            except OSError:
                pass
    return jsonify({"ok": True, "removed": removed})


@api_bp.route("/api/files/audio-count")
@require_auth
def api_files_audio_count():
    paths = request.args.get("paths", "").strip()
    if not paths:
        return jsonify({"counts": {}})
    path_list = [p.strip().lstrip("/") for p in paths.split("|") if p.strip()]
    _lib_path = get_current_library_path() or ""
    base = Path(_lib_path)

    def _audio_count_for_rel(rel: str):
        rel_normalized = rel.replace("/", "\\") if "\\" in _lib_path else rel
        cur = (base / rel_normalized).resolve()
        if not _is_within(base, cur) or not cur.is_dir():
            return rel, 0
        try:
            files = list(cur.rglob("*"))
            audio_files = [
                f
                for f in files
                if f.is_file() and f.suffix.lower().lstrip(".") in ("mp3", "flac", "m4a")
            ]
            return rel, len(audio_files)
        except OSError:
            return rel, 0

    # 目录音频计数为纯 IO 扫描，多个目录并行统计可显著加速
    counts = {}
    futures = [_API_EXECUTOR.submit(_audio_count_for_rel, rel) for rel in path_list]
    for future in as_completed(futures):
        rel, count = future.result()
        counts[rel] = count
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

    library_path = get_current_library_path() or ""

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
                "relative_path": get_relative_path(row["path"], library_path),
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
                "_log_msg": f"批量搜索出错: {row['filename']} - {e!s}",
            }

    results = []
    futures = {
        _API_EXECUTOR.submit(scrape_single_track, track_id): track_id
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
    from repository.task_repository import commit, set_task_config

    data = request.get_json(force=True)
    scrape_enabled = int(data.get("scrape_enabled", False))
    organize_enabled = int(data.get("organize_enabled", False))
    interval_minutes = int(data.get("interval_minutes", 60))

    # 时间间隔最小为5分钟
    interval_minutes = max(interval_minutes, 5)

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


# Library management
@api_bp.route("/api/libraries", methods=["GET"])
@require_auth
def api_libraries_list():
    libraries = get_all_libraries()
    current_id = get_current_library_id()
    result = []
    for lib in libraries:
        d = dict(lib)
        d["is_current"] = lib["id"] == current_id
        result.append(d)
    return jsonify(result)


@api_bp.route("/api/libraries/current", methods=["GET"])
@require_auth
def api_libraries_current():
    lib = get_current_library()
    if not lib:
        return jsonify(None)
    return jsonify(dict(lib))


@api_bp.route("/api/libraries", methods=["POST"])
@require_auth
def api_libraries_create():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    path = data.get("path", "").strip()
    if not name or not path:
        return jsonify({"ok": False, "error": "name and path required"}), 400
    is_default = 1 if data.get("is_default") else 0
    needs_config = 0
    if not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return jsonify({"ok": False, "error": f"无法创建路径: {path}"}), 400
    new_id = insert_library(
        name, path, is_default=is_default, needs_config=needs_config
    )
    return jsonify({"ok": True, "id": new_id})


@api_bp.route("/api/libraries/<int:library_id>", methods=["PUT"])
@require_auth
def api_libraries_update(library_id: int):
    lib = get_library_by_id(library_id)
    if not lib:
        abort(404)
    data = request.get_json(force=True)
    fields = {}
    if "name" in data:
        fields["name"] = data["name"].strip()
    if "path" in data:
        path = data["path"].strip()
        if not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                return jsonify({"ok": False, "error": f"无法创建路径: {path}"}), 400
        fields["path"] = path
        fields["needs_config"] = 0
    if "needs_config" in data:
        fields["needs_config"] = 1 if data["needs_config"] else 0
    update_library(library_id, **fields)
    return jsonify({"ok": True})


@api_bp.route("/api/libraries/<int:library_id>", methods=["DELETE"])
@require_auth
def api_libraries_delete(library_id: int):
    lib = get_library_by_id(library_id)
    if not lib:
        abort(404)
    if lib["is_default"]:
        return jsonify({"ok": False, "error": "默认音乐库不可删除"}), 400
    delete_library_repo(library_id)
    return jsonify({"ok": True})


@api_bp.route("/api/libraries/<int:library_id>/switch", methods=["POST"])
@require_auth
def api_libraries_switch(library_id: int):
    lib = get_library_by_id(library_id)
    if not lib:
        abort(404)
    set_current_library_id(library_id)
    return jsonify({"ok": True})
