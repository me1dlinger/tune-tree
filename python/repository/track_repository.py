"""
Track 数据访问层
封装所有与 tracks 表相关的 SQL 操作
"""

import os
from models.db import get_db
from utils.metadata import normalize_str
from utils.formatting import safe_dirname

# === Track CRUD 操作 ===


def get_track_by_id(track_id: int):
    """根据 ID 获取单个 track"""
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()


def get_track_by_path(path: str):
    """根据路径获取单个 track"""
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE path=?", (path,)).fetchone()


def get_track_by_filename_and_artist(filename: str, artist: str):
    """根据文件名和艺术家获取 track"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE filename=? AND artist=? LIMIT 1", (filename, artist)
    ).fetchone()


def get_track_by_filename_and_album(filename: str, album: str):
    """根据文件名和专辑获取 track"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE filename=? AND album=? LIMIT 1", (filename, album)
    ).fetchone()


def get_track_by_filename(filename: str):
    """根据文件名获取 track"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE filename=? LIMIT 1", (filename,)
    ).fetchone()


def get_track_id_and_mtime_by_path(path: str):
    """根据路径获取 track 的 ID 和修改时间"""
    db = get_db()
    return db.execute("SELECT id, mtime FROM tracks WHERE path=?", (path,)).fetchone()


def get_all_track_paths(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT path FROM tracks WHERE library_id=?",
            (library_id,),
        ).fetchall()
    return db.execute("SELECT path FROM tracks").fetchall()


def get_tracks_by_ids(track_ids: list[int]):
    db = get_db()
    placeholders = ",".join("?" * len(track_ids))
    return db.execute(
        f"SELECT t.* FROM tracks t LEFT JOIN artists a ON t.artist_id = a.id LEFT JOIN albums al ON t.album_id = al.id WHERE t.id IN ({placeholders}) ORDER BY a.name, al.title, t.disc_num, t.track_num, t.filename",
        track_ids,
    ).fetchall()


def get_tracks_by_artist_and_album(artist: str, album: str):
    db = get_db()
    return db.execute(
        """
        SELECT t.* FROM tracks t
        JOIN artists a ON t.artist_id = a.id
        JOIN albums al ON t.album_id = al.id
        WHERE a.name_normalized=? AND al.title_normalized=?
        ORDER BY t.disc_num, t.track_num, t.filename
    """,
        (normalize_str(artist), normalize_str(album)),
    ).fetchall()


def get_tracks_by_artist_and_album_id(artist: str, album_id: int):
    db = get_db()
    return db.execute(
        """
        SELECT t.* FROM tracks t
        JOIN artists a ON t.artist_id = a.id
        WHERE a.name_normalized=? AND t.album_id=?
        ORDER BY t.disc_num, t.track_num, t.filename
    """,
        (normalize_str(artist), album_id),
    ).fetchall()


def get_tracks_by_artist(artist: str):
    db = get_db()
    return db.execute(
        """
        SELECT t.* FROM tracks t
        JOIN artists a ON t.artist_id = a.id
        WHERE a.name_normalized=?
    """,
        (normalize_str(artist),),
    ).fetchall()


def get_tracks_by_artist_id(artist_id: int):
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE artist_id=? ORDER BY album_id, disc_num, track_num, filename",
        (artist_id,),
    ).fetchall()


def get_tracks_by_album_id(album_id: int):
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE album_id=? ORDER BY disc_num, track_num, filename",
        (album_id,),
    ).fetchall()


def get_pending_tracks(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT * FROM tracks WHERE pending=1 AND library_id=? ORDER BY filename",
            (library_id,),
        ).fetchall()
    return db.execute(
        "SELECT * FROM tracks WHERE pending=1 ORDER BY filename"
    ).fetchall()


def get_duplicate_tracks(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            """
            SELECT * FROM tracks
            WHERE library_id=? AND (LOWER(title)||'|'||LOWER(COALESCE(artist,''))||'|'||LOWER(COALESCE(album,''))) IN (
              SELECT LOWER(title)||'|'||LOWER(COALESCE(artist,''))||'|'||LOWER(COALESCE(album,''))
              FROM tracks WHERE library_id=? AND title IS NOT NULL AND artist IS NOT NULL AND album IS NOT NULL
              GROUP BY LOWER(title),LOWER(COALESCE(artist,'')),LOWER(COALESCE(album,''))
              HAVING COUNT(*) > 1
            )
            ORDER BY artist, album, title, path
        """,
            (library_id, library_id),
        ).fetchall()
    return db.execute("""
        SELECT * FROM tracks
        WHERE (LOWER(title)||'|'||LOWER(COALESCE(artist,''))||'|'||LOWER(COALESCE(album,''))) IN (
          SELECT LOWER(title)||'|'||LOWER(COALESCE(artist,''))||'|'||LOWER(COALESCE(album,''))
          FROM tracks WHERE title IS NOT NULL AND artist IS NOT NULL AND album IS NOT NULL
          GROUP BY LOWER(title),LOWER(COALESCE(artist,'')),LOWER(COALESCE(album,''))
          HAVING COUNT(*) > 1
        )
        ORDER BY artist, album, title, path
    """).fetchall()


def insert_track(
    path: str,
    filename: str,
    ext: str,
    size: int,
    mtime: float,
    ctime: float,
    title: str,
    artist: str | None,
    album: str | None,
    album_artist: str | None,
    year: str | None,
    track_num: int | None,
    disc_num: int | None,
    duration: float | None,
    sample_rate: int | None,
    bitrate: int | None,
    has_cover: int,
    has_lyrics: int,
    pending: int,
    missing_tags: str | None,
    scanned_at: float,
    artist_id: int | None = None,
    album_id: int | None = None,
    track_artist: str | None = None,
    library_id: int | None = None,
):
    """插入新的 track"""
    db = get_db()
    db.execute(
        """
        INSERT INTO tracks
        (path,filename,ext,size,mtime,ctime,title,artist,album,album_artist,year,
         track_num,disc_num,duration,sample_rate,bitrate,has_cover,has_lyrics,
         pending,missing_tags,scanned_at,artist_id,album_id,track_artist,library_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
        (
            path,
            filename,
            ext,
            size,
            mtime,
            ctime,
            title,
            artist,
            album,
            album_artist,
            year,
            track_num,
            disc_num,
            duration,
            sample_rate,
            bitrate,
            has_cover,
            has_lyrics,
            pending,
            missing_tags,
            scanned_at,
            artist_id,
            album_id,
            track_artist,
            library_id,
        ),
    )
    db.commit()


def update_track_by_path(
    filename: str,
    ext: str,
    size: int,
    mtime: float,
    ctime: float,
    title: str,
    artist: str | None,
    album: str | None,
    album_artist: str | None,
    year: str | None,
    track_num: int | None,
    disc_num: int | None,
    duration: float | None,
    sample_rate: int | None,
    bitrate: int | None,
    has_cover: int,
    has_lyrics: int,
    pending: int,
    missing_tags: str | None,
    scanned_at: float,
    path: str,
    artist_id: int | None = None,
    album_id: int | None = None,
    track_artist: str | None = None,
    library_id: int | None = None,
):
    """根据路径更新 track"""
    db = get_db()
    db.execute(
        """
        UPDATE tracks SET filename=?,ext=?,size=?,mtime=?,ctime=?,title=?,artist=?,
        album=?,album_artist=?,year=?,track_num=?,disc_num=?,duration=?,
        sample_rate=?,bitrate=?,has_cover=?,has_lyrics=?,pending=?,
        missing_tags=?,scanned_at=?,artist_id=?,album_id=?,track_artist=?,
        library_id=COALESCE(?,library_id)
        WHERE path=?
    """,
        (
            filename,
            ext,
            size,
            mtime,
            ctime,
            title,
            artist,
            album,
            album_artist,
            year,
            track_num,
            disc_num,
            duration,
            sample_rate,
            bitrate,
            has_cover,
            has_lyrics,
            pending,
            missing_tags,
            scanned_at,
            artist_id,
            album_id,
            track_artist,
            library_id,
            path,
        ),
    )
    db.commit()


def update_track_path_and_name(track_id: int, new_path: str, new_filename: str):
    """更新 track 的路径和文件名，并标记为已整理"""
    db = get_db()
    db.execute(
        "UPDATE tracks SET path=?,filename=?,organized=1 WHERE id=?",
        (new_path, new_filename, track_id),
    )
    db.commit()


FORBIDDEN_UPDATE_FIELDS = {
    "path",
    "filename",
    "ext",
    "size",
    "mtime",
    "ctime",
}


def update_track_metadata(track_id: int, fields: dict):
    """按需更新 track 的元数据字段，禁止更新文件属性字段"""
    if not fields:
        return
    allowed = {k: v for k, v in fields.items() if k not in FORBIDDEN_UPDATE_FIELDS}
    if not allowed:
        return
    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in allowed)
    values = list(allowed.values()) + [track_id]
    db.execute(f"UPDATE tracks SET {set_clause} WHERE id=?", values)
    db.commit()


def recalc_pending(track_id: int):
    """根据当前 track 数据重新计算 pending 和 missing_tags"""
    row = get_track_by_id(track_id)
    if not row:
        return
    missing = []
    if not row["title"]:
        missing.append("title")
    if not row["artist"]:
        missing.append("artist")
    if not row["album"]:
        missing.append("album")
    pending = 1 if missing else 0
    missing_str = ",".join(missing)
    db = get_db()
    db.execute(
        "UPDATE tracks SET pending=?, missing_tags=? WHERE id=?",
        (pending, missing_str, track_id),
    )
    db.commit()


def delete_track_by_path(path: str):
    """根据路径删除 track"""
    db = get_db()
    db.execute("DELETE FROM tracks WHERE path=?", (path,))
    db.commit()


def delete_track_by_id(track_id: int):
    """根据 ID 删除 track（同时删除本地文件）"""
    db = get_db()
    row = db.execute("SELECT path FROM tracks WHERE id=?", (track_id,)).fetchone()
    if row:
        import os

        file_path = row["path"]
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
    db.execute("DELETE FROM tracks WHERE id=?", (track_id,))
    db.commit()


def get_artist_by_track_id(track_id: int):
    db = get_db()
    row = db.execute(
        "SELECT a.name FROM tracks t JOIN artists a ON t.artist_id = a.id WHERE t.id=?",
        (track_id,),
    ).fetchone()
    return row["name"] if row else None


def count_tracks_by_artist_with_status(artist: str, organized: int, pending: int):
    db = get_db()
    return db.execute(
        """
        SELECT COUNT(*) as c FROM tracks t
        JOIN artists a ON t.artist_id = a.id
        WHERE a.name_normalized=? AND t.organized=? AND t.pending=?
    """,
        (normalize_str(artist), organized, pending),
    ).fetchone()["c"]


def count_tracks_by_artist_id_with_status(artist_id: int, organized: int, pending: int):
    db = get_db()
    return db.execute(
        "SELECT COUNT(*) as c FROM tracks WHERE artist_id=? AND organized=? AND pending=?",
        (artist_id, organized, pending),
    ).fetchone()["c"]


# === Artist 相关操作 ===


def get_artists(query: str | None = None, library_id: int | None = None):
    from repository.artist_repository import get_all_artists

    return get_all_artists(query, library_id=library_id)


# === Album 相关操作 ===


def get_albums_by_artist(artist: str):
    from repository.artist_repository import get_artist_by_name
    from repository.album_repository import get_albums_by_artist_id

    a = get_artist_by_name(artist)
    if not a:
        return []
    return get_albums_by_artist_id(a["id"])


def get_artist_full_info(artist: str):
    from repository.artist_repository import get_artist_by_name
    from repository.album_repository import get_albums_by_artist_id

    a = get_artist_by_name(artist)
    if not a:
        return {"artist": artist, "albums": []}
    albums = get_albums_by_artist_id(a["id"])
    tracks = get_tracks_by_artist_id(a["id"])
    albums_with_tracks = []
    for album in albums:
        album_dict = dict(album)
        album_dict["tracks"] = [dict(t) for t in tracks if t["album_id"] == album["id"]]
        albums_with_tracks.append(album_dict)
    return {"artist": artist, "albums": albums_with_tracks}


def get_artist_full_info_by_id(artist_id: int):
    from repository.artist_repository import get_artist_by_id
    from repository.album_repository import get_albums_by_artist_id

    a = get_artist_by_id(artist_id)
    if not a:
        return None
    albums = get_albums_by_artist_id(artist_id)

    tracks = get_tracks_by_artist_id(artist_id)
    albums_with_tracks = []
    for album in albums:
        album_dict = dict(album)
        album_dict["tracks"] = [dict(t) for t in tracks if t["album_id"] == album["id"]]
        albums_with_tracks.append(album_dict)
    result = dict(a)
    result["albums"] = albums_with_tracks
    return result


def get_artist_directory_path(artist: str, music_root: str) -> str | None:
    from repository.artist_repository import get_artist_by_name

    a = get_artist_by_name(artist)
    if a:
        artist_dir = os.path.join(music_root, a["dir_name"])
        os.makedirs(artist_dir, exist_ok=True)
        return artist_dir
    artist_dir = os.path.join(music_root, safe_dirname(artist))
    os.makedirs(artist_dir, exist_ok=True)
    return artist_dir


def get_artist_directory_path_by_id(
    artist_id: int, music_root: str = None
) -> str | None:
    from repository.artist_repository import get_artist_by_id

    a = get_artist_by_id(artist_id)
    if not a:
        return None
    if music_root is None:
        from repository.library_repository import get_current_library_path

        music_root = get_current_library_path()
    if not music_root:
        return None
    artist_dir = os.path.join(music_root, a["dir_name"])
    os.makedirs(artist_dir, exist_ok=True)
    return artist_dir


# === 统计操作 ===


def count_total_tracks(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT COUNT(*) FROM tracks WHERE library_id=?",
            (library_id,),
        ).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


def count_total_artists(library_id: int | None = None):
    from repository.artist_repository import count_total_artists as _count

    return _count(library_id)


def count_total_albums(library_id: int | None = None):
    from repository.album_repository import count_total_albums as _count

    return _count(library_id)


def count_pending_tracks(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT COUNT(*) FROM tracks WHERE pending=1 AND library_id=?",
            (library_id,),
        ).fetchone()[0]
    return db.execute("SELECT COUNT(*) FROM tracks WHERE pending=1").fetchone()[0]


def count_organized_artists(library_id: int | None = None):
    from repository.artist_repository import count_organized_artists as _count

    return _count(library_id)


def count_organized_albums(library_id: int | None = None):
    from repository.album_repository import count_organized_albums as _count

    return _count(library_id)


def count_duplicate_groups(library_id: int | None = None):
    db = get_db()
    if library_id is not None:
        return db.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT title,artist FROM tracks
              WHERE title IS NOT NULL AND artist IS NOT NULL AND library_id=?
              GROUP BY LOWER(title),LOWER(artist),LOWER(album)
              HAVING COUNT(*) > 1
            )
        """,
            (library_id,),
        ).fetchone()[0]
    return db.execute("""
        SELECT COUNT(*) FROM (
          SELECT title,artist FROM tracks
          WHERE title IS NOT NULL AND artist IS NOT NULL
          GROUP BY LOWER(title),LOWER(artist),LOWER(album)
          HAVING COUNT(*) > 1
        )
    """).fetchone()[0]


def count_tracks_by_extension(ext: str, library_id: int | None = None):
    db = get_db()
    normalized_ext = ext.lower()
    lib_filter = "AND library_id=?" if library_id is not None else ""
    params_base = [library_id] if library_id is not None else []
    if normalized_ext.startswith("."):
        ext_without_dot = normalized_ext[1:]
        return db.execute(
            f"SELECT COUNT(*) FROM tracks WHERE (ext=? OR ext=?) {lib_filter}",
            [normalized_ext, ext_without_dot] + params_base,
        ).fetchone()[0]
    else:
        ext_with_dot = "." + normalized_ext
        return db.execute(
            f"SELECT COUNT(*) FROM tracks WHERE (ext=? OR ext=?) {lib_filter}",
            [normalized_ext, ext_with_dot] + params_base,
        ).fetchone()[0]


# === Scan Meta 操作 ===


def get_scan_meta(key: str):
    """获取扫描元数据"""
    db = get_db()
    row = db.execute("SELECT value FROM scan_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_scan_meta(key: str, value: str):
    """设置扫描元数据"""
    db = get_db()
    db.execute("INSERT OR REPLACE INTO scan_meta VALUES (?,?)", (key, value))
    db.commit()


# === Scan Status 操作 ===

SCAN_STATUS_KEY = "scan_status"
SCAN_START_TIME_KEY = "scan_start_time"


def get_scan_status() -> dict:
    """获取扫描状态"""
    db = get_db()
    status = db.execute(
        "SELECT value FROM scan_meta WHERE key=?", (SCAN_STATUS_KEY,)
    ).fetchone()
    start_time = db.execute(
        "SELECT value FROM scan_meta WHERE key=?", (SCAN_START_TIME_KEY,)
    ).fetchone()

    start_time_value = None
    if start_time and start_time["value"]:
        try:
            start_time_value = float(start_time["value"])
        except (ValueError, TypeError):
            start_time_value = None

    return {
        "scanning": status["value"] == "running" if status else False,
        "start_time": start_time_value,
    }


def set_scan_running(start_time: float):
    """标记扫描开始"""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO scan_meta VALUES (?,?)", (SCAN_STATUS_KEY, "running")
    )
    db.execute(
        "INSERT OR REPLACE INTO scan_meta VALUES (?,?)",
        (SCAN_START_TIME_KEY, str(start_time)),
    )
    db.commit()


def set_scan_finished():
    """标记扫描结束"""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO scan_meta VALUES (?,?)", (SCAN_STATUS_KEY, "idle")
    )
    db.execute(
        "INSERT OR REPLACE INTO scan_meta VALUES (?,?)", (SCAN_START_TIME_KEY, "")
    )
    db.commit()


# === Operation Log 操作 ===


def add_op_log(ts: str, op_type: str, message: str, library_id: int | None = None):
    """添加操作日志"""
    db = get_db()
    db.execute(
        "INSERT INTO op_log (ts,op_type,message,library_id) VALUES (?,?,?,?)",
        (ts, op_type, message, library_id),
    )
    db.commit()


def get_op_logs(limit: int = 200, library_id: int | None = None):
    """获取操作日志"""
    db = get_db()
    if library_id is not None:
        return db.execute(
            "SELECT * FROM op_log WHERE library_id=? OR library_id IS NULL ORDER BY id DESC LIMIT ?",
            (library_id, limit),
        ).fetchall()
    return db.execute(
        "SELECT * FROM op_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def clear_op_logs(library_id: int | None = None):
    """清空操作日志"""
    db = get_db()
    if library_id is not None:
        db.execute("DELETE FROM op_log WHERE library_id=?", (library_id,))
    else:
        db.execute("DELETE FROM op_log")
    db.commit()


def commit():
    """提交事务"""
    db = get_db()
    db.commit()
