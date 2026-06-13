"""
元数据处理工具
"""

import base64
import logging
import re
import unicodedata
from pathlib import Path
from mutagen import File as MutagenFile

logger = logging.getLogger("tunetree")


def _normalize_path(path: str) -> str:
    """对路径进行Unicode正规化，处理日语假名等字符的不同表示形式"""
    return unicodedata.normalize("NFC", path)


INVISIBLE_CHARS = {
    0x00A0,  # Non-breaking space
    0x2000,
    0x2001,
    0x2002,
    0x2003,
    0x2004,
    0x2005,
    0x2006,
    0x2007,
    0x2008,
    0x2009,
    0x200A,  # Various spaces
    0x202F,  # Narrow no-break space
    0x205F,  # Medium mathematical space
    0x3000,  # Ideographic space (fullwidth space)
    0x2028,  # Line separator
    0x2029,  # Paragraph separator
    0x200B,  # Zero width space
    0x200C,
    0x200D,  # Zero width non-joiner / joiner
    0xFEFF,  # Byte order mark / zero width no-break space
    0x180E,  # Mongolian free variation selector
    0x2060,  # Word joiner
    0x2061,
    0x2062,
    0x2063,
    0x2064,  # Function application / invisible times / etc.
    0x206A,
    0x206B,
    0x206C,
    0x206D,
    0x206E,
    0x206F,  # Invisible operators
}


def _remove_invisible(text: str) -> str:
    """移除字符串中的不可见Unicode字符"""
    return "".join(c for c in text if ord(c) not in INVISIBLE_CHARS)


def normalize_str(text: str) -> str:
    """对字符串进行Unicode正规化，用于比较和存储
    使用NFKC正规化，可以将兼容字符统一（如全角转半角）
    同时移除不可见字符"""
    if not text:
        return text
    normalized = unicodedata.normalize("NFKC", text)
    return _remove_invisible(normalized)


def _find_file(path: str) -> str:
    """尝试多种Unicode正规化形式查找文件，返回可访问的路径"""
    # 尝试原始路径
    if Path(path).exists():
        return path

    # 尝试NFC正规化
    nfc_path = unicodedata.normalize("NFC", path)
    if Path(nfc_path).exists():
        return nfc_path

    # 尝试NFD正规化
    nfd_path = unicodedata.normalize("NFD", path)
    if Path(nfd_path).exists():
        return nfd_path

    # 尝试NFKC正规化
    nfkc_path = unicodedata.normalize("NFKC", path)
    if Path(nfkc_path).exists():
        return nfkc_path

    # 尝试NFKD正规化
    nfkd_path = unicodedata.normalize("NFKD", path)
    if Path(nfkd_path).exists():
        return nfkd_path

    # 都找不到，返回原始路径
    return path


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
        "title": None,
        "artist": None,
        "album": None,
        "album_artist": None,
        "year": None,
        "track_num": None,
        "disc_num": None,
        "duration": None,
        "sample_rate": None,
        "bitrate": None,
        "has_cover": 0,
        "has_lyrics": 0,
    }
    try:
        actual_path = _find_file(path)
        audio = MutagenFile(actual_path, easy=True)
        if audio is None:
            return meta

        meta["title"] = _safe_str(audio.get("title"))
        meta["artist"] = _safe_str(audio.get("artist"))
        meta["album"] = _safe_str(audio.get("album"))
        meta["album_artist"] = _safe_str(audio.get("albumartist"))
        meta["year"] = _safe_str(audio.get("date") or audio.get("year"))
        meta["track_num"] = _parse_track_num(audio.get("tracknumber"))
        meta["disc_num"] = _parse_track_num(audio.get("discnumber"))

        if hasattr(audio, "info"):
            info = audio.info
            meta["duration"] = getattr(info, "length", None)
            meta["sample_rate"] = getattr(info, "sample_rate", None)
            meta["bitrate"] = getattr(info, "bitrate", None)

        # check cover / lyrics via non-easy tags
        raw = MutagenFile(actual_path)
        if raw:
            ext = Path(actual_path).suffix.lower()
            if ext == ".flac":
                meta["has_cover"] = 1 if raw.pictures else 0
                meta["has_lyrics"] = (
                    1 if raw.get("lyrics") or raw.get("unsyncedlyrics") else 0
                )
            elif ext == ".mp3":
                tags = raw.tags or {}
                meta["has_cover"] = 1 if any(k.startswith("APIC") for k in tags) else 0
                meta["has_lyrics"] = 1 if any(k.startswith("USLT") for k in tags) else 0

    except Exception as exc:
        logger.warning("metadata read error %s: %s", path, exc)

    return meta


def get_cover_b64(path: str) -> str | None:
    """Extract embedded album art, return base64-encoded JPEG/PNG or None."""
    try:
        actual_path = _find_file(path)
        ext = Path(actual_path).suffix.lower()
        raw = MutagenFile(actual_path)
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


def extract_cover_to_file(track_path: str, output_path: str) -> bool:
    """Extract embedded cover art from a track and save to output_path.
    Returns True if cover was extracted and saved successfully."""
    try:
        actual_path = _find_file(track_path)
        ext = Path(actual_path).suffix.lower()
        raw = MutagenFile(actual_path)
        if raw is None:
            return False
        cover_data = None
        if ext == ".flac":
            pics = raw.pictures
            if pics:
                cover_data = pics[0].data
        elif ext == ".mp3":
            tags = raw.tags or {}
            for k, v in tags.items():
                if k.startswith("APIC"):
                    cover_data = v.data
                    break
        if not cover_data:
            return False
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(cover_data))
        if img.format != "JPEG":
            img = img.convert("RGB")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "JPEG", quality=90)
        return True
    except Exception as exc:
        logger.warning("cover extract to file error %s: %s", track_path, exc)
        return False


TAG_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "albumartist",
    "year": "date",
    "track_num": "tracknumber",
}


def write_metadata(path: str, fields: dict) -> dict:
    actual_path = _find_file(path)
    logger.info(f"write metadata to {actual_path}")
    """Write metadata tags to mp3/flac. fields keys: artist/album/album_artist/year/track_num.
    Returns dict of actually updated fields."""
    if not fields:
        return {}
    audio = MutagenFile(actual_path, easy=True)
    if audio is None:
        raise ValueError(f"Cannot open audio file: {path}")
    updated = {}
    for src_key, tag_key in TAG_MAP.items():
        if src_key not in fields:
            continue
        val = fields[src_key]
        if val is None or val == "":
            audio.pop(tag_key, None)
            updated[src_key] = None
        else:
            audio[tag_key] = [str(val)]
            updated[src_key] = str(val)
    if updated:
        audio.save()
    return updated


def write_cover(path: str, image_data: bytes, mime_type: str) -> None:
    """Write embedded cover art to mp3/flac. Removes existing cover first."""
    actual_path = _find_file(path)
    ext = Path(actual_path).suffix.lower()
    raw = MutagenFile(actual_path)
    if raw is None:
        raise ValueError(f"Cannot open audio file: {path}")
    if ext == ".flac":
        raw.clear_pictures()
        from mutagen.flac import Picture

        pic = Picture()
        pic.data = image_data
        pic.mime = mime_type
        pic.type = 3
        raw.add_picture(pic)
        raw.save()
    elif ext == ".mp3":
        from mutagen.id3 import APIC, ID3

        tags = ID3(actual_path)
        for k in [k for k in tags if k.startswith("APIC")]:
            del tags[k]
        tags.add(
            APIC(
                encoding=3,
                mime=mime_type,
                type=3,
                desc="Cover",
                data=image_data,
            )
        )
        tags.save(actual_path)
    else:
        raise ValueError(f"Unsupported format for cover write: {ext}")


def write_lyrics(path: str, lyrics_text: str) -> None:
    """Write lyrics tag to mp3/flac. Empty string removes lyrics."""
    actual_path = _find_file(path)
    ext = Path(actual_path).suffix.lower()
    raw = MutagenFile(actual_path)
    if raw is None:
        raise ValueError(f"Cannot open audio file: {path}")
    if ext == ".flac":
        if lyrics_text:
            raw["lyrics"] = [lyrics_text]
        else:
            raw.pop("lyrics", None)
            raw.pop("unsyncedlyrics", None)
        raw.save()
    elif ext == ".mp3":
        from mutagen.id3 import USLT, ID3

        tags = ID3(actual_path)
        to_remove = [k for k in tags if k.startswith("USLT")]
        for k in to_remove:
            del tags[k]
        if lyrics_text:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_text))
        tags.save(actual_path)
    else:
        raise ValueError(f"Unsupported format for lyrics write: {ext}")


def get_lyrics(path: str) -> str | None:
    try:
        actual_path = _find_file(path)
        ext = Path(actual_path).suffix.lower()
        raw = MutagenFile(actual_path)
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
