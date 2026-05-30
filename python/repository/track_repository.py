"""
Track 数据访问层
封装所有与 tracks 表相关的 SQL 操作
"""

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


def get_all_track_paths():
    """获取所有 track 的路径"""
    db = get_db()
    return db.execute("SELECT path FROM tracks").fetchall()


def get_tracks_by_ids(track_ids: list[int]):
    """根据 ID 列表获取 tracks"""
    db = get_db()
    placeholders = ",".join("?" * len(track_ids))
    return db.execute(
        f"SELECT * FROM tracks WHERE id IN ({placeholders}) ORDER BY artist, album, disc_num, track_num, filename",
        track_ids,
    ).fetchall()


def get_tracks_by_artist_and_album(artist: str, album: str):
    """根据艺术家和专辑获取 tracks"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE artist=? AND album=? ORDER BY disc_num, track_num, filename",
        (artist, album),
    ).fetchall()


def get_tracks_by_artist_and_album_id(artist: str, album_id: int):
    """根据艺术家和专辑 ID 获取 tracks"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE artist=? AND album=(SELECT album FROM tracks WHERE id=? LIMIT 1) ORDER BY disc_num, track_num, filename",
        (artist, album_id),
    ).fetchall()


def get_tracks_by_artist(artist: str):
    """根据艺术家获取 tracks"""
    db = get_db()
    return db.execute("SELECT * FROM tracks WHERE artist=?", (artist,)).fetchall()


def get_pending_tracks():
    """获取待处理的 tracks"""
    db = get_db()
    return db.execute(
        "SELECT * FROM tracks WHERE pending=1 ORDER BY filename"
    ).fetchall()


def get_duplicate_tracks():
    """获取重复的 tracks"""
    db = get_db()
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
    artist: str,
    album: str,
    album_artist: str,
    year: str,
    track_num: int,
    disc_num: int,
    duration: float,
    sample_rate: int,
    bitrate: int,
    has_cover: int,
    has_lyrics: int,
    pending: int,
    missing_tags: str,
    scanned_at: float,
):
    """插入新的 track"""
    db = get_db()
    db.execute(
        """
        INSERT INTO tracks
        (path,filename,ext,size,mtime,ctime,title,artist,album,album_artist,year,
         track_num,disc_num,duration,sample_rate,bitrate,has_cover,has_lyrics,
         pending,missing_tags,scanned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        ),
    )


def update_track_by_path(
    filename: str,
    ext: str,
    size: int,
    mtime: float,
    ctime: float,
    title: str,
    artist: str,
    album: str,
    album_artist: str,
    year: str,
    track_num: int,
    disc_num: int,
    duration: float,
    sample_rate: int,
    bitrate: int,
    has_cover: int,
    has_lyrics: int,
    pending: int,
    missing_tags: str,
    scanned_at: float,
    path: str,
):
    """根据路径更新 track"""
    db = get_db()
    db.execute(
        """
        UPDATE tracks SET filename=?,ext=?,size=?,mtime=?,ctime=?,title=?,artist=?,
        album=?,album_artist=?,year=?,track_num=?,disc_num=?,duration=?,
        sample_rate=?,bitrate=?,has_cover=?,has_lyrics=?,pending=?,
        missing_tags=?,scanned_at=?
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
            path,
        ),
    )


def update_track_path_and_name(track_id: int, new_path: str, new_filename: str):
    """更新 track 的路径和文件名，并标记为已整理"""
    db = get_db()
    db.execute(
        "UPDATE tracks SET path=?,filename=?,organized=1 WHERE id=?",
        (new_path, new_filename, track_id),
    )


FORBIDDEN_UPDATE_FIELDS = {"path", "filename", "ext", "size", "mtime", "ctime"}


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


def delete_track_by_path(path: str):
    """根据路径删除 track"""
    db = get_db()
    db.execute("DELETE FROM tracks WHERE path=?", (path,))


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


def get_artist_by_track_id(track_id: int):
    """根据 track ID 获取艺术家"""
    db = get_db()
    row = db.execute("SELECT artist FROM tracks WHERE id=?", (track_id,)).fetchone()
    return row["artist"] if row else None


def count_tracks_by_artist_with_status(artist: str, organized: int, pending: int):
    """统计艺术家特定状态的 tracks 数量"""
    db = get_db()
    return db.execute(
        "SELECT COUNT(*) as c FROM tracks WHERE artist=? AND organized=? AND pending=?",
        (artist, organized, pending),
    ).fetchone()["c"]


# === Artist 相关操作 ===


def get_artists(query: str = None):
    """获取艺术家列表，支持搜索"""
    db = get_db()
    if query:
        return db.execute(
            """
            SELECT artist,
                   COUNT(DISTINCT album) AS album_count,
                   COUNT(*) AS track_count,
                   MIN(CASE WHEN organized=0 AND pending=0 THEN 0 ELSE 1 END) AS all_organized,
                   MAX(ctime) AS last_created_at
            FROM tracks
            WHERE artist IS NOT NULL AND artist != '' AND artist LIKE ?
            GROUP BY artist
            ORDER BY artist COLLATE NOCASE
        """,
            (f"%{query}%",),
        ).fetchall()
    else:
        return db.execute("""
            SELECT artist,
                   COUNT(DISTINCT album) AS album_count,
                   COUNT(*) AS track_count,
                   MIN(CASE WHEN organized=0 AND pending=0 THEN 0 ELSE 1 END) AS all_organized,
                   MAX(ctime) AS last_created_at
            FROM tracks
            WHERE artist IS NOT NULL AND artist != ''
            GROUP BY artist
            ORDER BY artist COLLATE NOCASE
        """).fetchall()


# === Album 相关操作 ===


def get_albums_by_artist(artist: str):
    """根据艺术家获取专辑列表"""
    db = get_db()
    return db.execute(
        """
        SELECT album,
               MIN(year) AS year,
               COUNT(*) AS track_count,
               MIN(has_cover) AS has_cover_some,
               MIN(CASE WHEN organized=0 AND pending=0 THEN 0 ELSE 1 END) AS all_organized,
               MIN(id) AS sample_id
        FROM tracks
        WHERE artist=? AND album IS NOT NULL AND album != ''
        GROUP BY album
        ORDER BY year, album COLLATE NOCASE
    """,
        (artist,),
    ).fetchall()


def get_artist_full_info(artist: str):
    """获取艺术家的完整信息，包括所有专辑及其下的所有歌曲"""
    db = get_db()

    albums = db.execute(
        """
        SELECT album,
               MIN(year) AS year,
               COUNT(*) AS track_count,
               MIN(has_cover) AS has_cover_some,
               MIN(CASE WHEN organized=0 AND pending=0 THEN 0 ELSE 1 END) AS all_organized,
               MIN(id) AS sample_id
        FROM tracks
        WHERE artist=? AND album IS NOT NULL AND album != ''
        GROUP BY album
        ORDER BY year, album COLLATE NOCASE
    """,
        (artist,),
    ).fetchall()

    tracks = db.execute(
        """
        SELECT * FROM tracks 
        WHERE artist=? 
        ORDER BY album, disc_num, track_num, filename
    """,
        (artist,),
    ).fetchall()

    albums_with_tracks = []
    for album in albums:
        album_dict = dict(album)
        album_dict["tracks"] = [dict(t) for t in tracks if t["album"] == album["album"]]
        albums_with_tracks.append(album_dict)

    return {"artist": artist, "albums": albums_with_tracks}


def get_artist_directory_path(artist: str) -> str | None:
    """获取艺术家的目录路径（通过任意一首歌曲的路径推断）
    
    假设目录结构为: music_root/ArtistName/AlbumName/song.flac
    """
    db = get_db()
    row = db.execute(
        "SELECT path FROM tracks WHERE artist=? LIMIT 1", (artist,)
    ).fetchone()
    if not row:
        return None
    
    from pathlib import Path
    track_path = Path(row["path"])
    parts = track_path.parts
    artist_safe = safe_dirname(artist)
    artist_norm = normalize_str(artist_safe.lower().strip())
    for i in range(len(parts) - 1, 0, -1):
        folder_name = parts[i]
        folder_safe = safe_dirname(folder_name)
        folder_norm = normalize_str(folder_safe.lower())
        if folder_norm == artist_norm:
            return str(Path(*parts[:i+1]))
    
    return str(track_path.parent.parent)


# === 统计操作 ===


def count_total_tracks():
    """统计总 track 数"""
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


def count_total_artists():
    """统计总艺术家数"""
    db = get_db()
    return db.execute(
        "SELECT COUNT(DISTINCT artist) FROM tracks WHERE artist IS NOT NULL AND artist!=''",
    ).fetchone()[0]


def count_total_albums():
    """统计总专辑数"""
    db = get_db()
    return db.execute(
        "SELECT COUNT(DISTINCT artist||'|'||album) FROM tracks WHERE artist IS NOT NULL AND album IS NOT NULL",
    ).fetchone()[0]


def count_pending_tracks():
    """统计待处理 track 数"""
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM tracks WHERE pending=1").fetchone()[0]


def count_organized_artists():
    """统计已整理的艺术家数"""
    db = get_db()
    return db.execute("""
        SELECT COUNT(*) FROM (
          SELECT artist FROM tracks
          WHERE artist IS NOT NULL AND artist!=''
          GROUP BY artist
          HAVING SUM(CASE WHEN organized=0 AND pending=0 THEN 1 ELSE 0 END) = 0
        )
    """).fetchone()[0]


def count_organized_albums():
    """统计已整理的专辑数"""
    db = get_db()
    return db.execute("""
        SELECT COUNT(*) FROM (
          SELECT artist,album FROM tracks
          WHERE artist IS NOT NULL AND album IS NOT NULL
          GROUP BY artist,album
          HAVING SUM(CASE WHEN organized=0 AND pending=0 THEN 1 ELSE 0 END) = 0
        )
    """).fetchone()[0]


def count_duplicate_groups():
    """统计重复分组数"""
    db = get_db()
    return db.execute("""
        SELECT COUNT(*) FROM (
          SELECT title,artist FROM tracks
          WHERE title IS NOT NULL AND artist IS NOT NULL
          GROUP BY LOWER(title),LOWER(artist),LOWER(album)
          HAVING COUNT(*) > 1
        )
    """).fetchone()[0]


def count_tracks_by_extension(ext: str):
    """按扩展名统计 track 数"""
    db = get_db()
    normalized_ext = ext.lower()
    if normalized_ext.startswith("."):
        ext_without_dot = normalized_ext[1:]
        return db.execute(
            "SELECT COUNT(*) FROM tracks WHERE ext=? OR ext=?",
            (normalized_ext, ext_without_dot),
        ).fetchone()[0]
    else:
        ext_with_dot = "." + normalized_ext
        return db.execute(
            "SELECT COUNT(*) FROM tracks WHERE ext=? OR ext=?",
            (normalized_ext, ext_with_dot),
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


def add_op_log(ts: str, op_type: str, message: str):
    """添加操作日志"""
    db = get_db()
    db.execute(
        "INSERT INTO op_log (ts,op_type,message) VALUES (?,?,?)", (ts, op_type, message)
    )


def get_op_logs(limit: int = 200):
    """获取操作日志"""
    db = get_db()
    return db.execute(
        "SELECT * FROM op_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def clear_op_logs():
    """清空操作日志"""
    db = get_db()
    db.execute("DELETE FROM op_log")


def commit():
    """提交事务"""
    db = get_db()
    db.commit()
