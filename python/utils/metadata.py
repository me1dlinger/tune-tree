"""
元数据处理工具
"""
import base64
import logging
import re
from pathlib import Path
from mutagen import File as MutagenFile

logger = logging.getLogger("tunetree")

def _safe_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, list):
        return str(val[0]) if val else None
    return str(val)

def _parse_track_num(val) -> int | None:
    s = _safe_str(val)
    if not s:
        return None
    m = re.match(r"(\d+)", s)
    return int(m.group(1)) if m else None

def read_metadata(path: str) -> dict:
    """Read tags from mp3/flac. Returns a dict of metadata."""
    meta: dict = {
        "title": None, "artist": None, "album": None,
        "album_artist": None, "year": None,
        "track_num": None, "disc_num": None,
        "duration": None, "sample_rate": None, "bitrate": None,
        "has_cover": 0, "has_lyrics": 0,
    }
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return meta

        meta["title"]        = _safe_str(audio.get("title"))
        meta["artist"]       = _safe_str(audio.get("artist"))
        meta["album"]        = _safe_str(audio.get("album"))
        meta["album_artist"] = _safe_str(audio.get("albumartist"))
        meta["year"]         = _safe_str(audio.get("date") or audio.get("year"))
        meta["track_num"]    = _parse_track_num(audio.get("tracknumber"))
        meta["disc_num"]     = _parse_track_num(audio.get("discnumber"))

        if hasattr(audio, "info"):
            info = audio.info
            meta["duration"]    = getattr(info, "length", None)
            meta["sample_rate"] = getattr(info, "sample_rate", None)
            meta["bitrate"]     = getattr(info, "bitrate", None)

        # check cover / lyrics via non-easy tags
        raw = MutagenFile(path)
        if raw:
            ext = Path(path).suffix.lower()
            if ext == ".flac":
                meta["has_cover"]  = 1 if raw.pictures else 0
                meta["has_lyrics"] = 1 if raw.get("lyrics") or raw.get("unsyncedlyrics") else 0
            elif ext == ".mp3":
                tags = raw.tags or {}
                meta["has_cover"]  = 1 if any(k.startswith("APIC") for k in tags) else 0
                meta["has_lyrics"] = 1 if any(k.startswith("USLT") for k in tags) else 0

    except Exception as exc:
        logger.warning("metadata read error %s: %s", path, exc)

    return meta

def get_cover_b64(path: str) -> str | None:
    """Extract embedded album art, return base64-encoded JPEG/PNG or None."""
    try:
        ext = Path(path).suffix.lower()
        raw = MutagenFile(path)
        if raw is None:
            return None
        if ext == ".flac":
            pics = raw.pictures
            if pics:
                data = pics[0].data
                mime = pics[0].mime or "image/jpeg"
                return f"data:{mime};base64," + base64.b64encode(data).decode()
        elif ext == ".mp3":
            tags = raw.tags or {}
            for k, v in tags.items():
                if k.startswith("APIC"):
                    return f"data:{v.mime};base64," + base64.b64encode(v.data).decode()
    except Exception as exc:
        logger.warning("cover extract error %s: %s", path, exc)
    return None

def get_lyrics(path: str) -> str | None:
    try:
        ext = Path(path).suffix.lower()
        raw = MutagenFile(path)
        if raw is None:
            return None
        if ext == ".flac":
            for key in ("lyrics", "unsyncedlyrics", "LYRICS"):
                val = raw.get(key)
                if val:
                    return str(val[0]) if isinstance(val, list) else str(val)
        elif ext == ".mp3":
            tags = raw.tags or {}
            for k, v in tags.items():
                if k.startswith("USLT"):
                    return v.text
    except Exception:
        pass
    return None
