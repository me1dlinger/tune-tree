"""
QQ音乐 API 模块
集成搜索、歌曲信息、歌词、歌手头像等功能
通过直接调用 QQ 音乐 HTTP 接口实现，无需额外依赖
"""

import re
import io
import base64
import logging
from typing import List, Dict, Optional
from PIL import Image
import requests

from utils.qrc_decrypt import qrc_decrypt

logger = logging.getLogger("tunetree")


class QQMusicApi:
    """
    QQ音乐 API 客户端
    使用 musicu.fcg 统一接口
    """

    _MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    _HEADERS = {
        "User-Agent": "okhttp/3.14.9",
        "Referer": "https://y.qq.com/",
        "Content-Type": "application/json",
        "Cookie": "tmeLoginType=-1;",
    }

    _COMM = {
        "ct": 11,
        "cv": "2111",
        "v": "2111",
        "os_ver": "15",
        "tmeAppID": "qqmusic",
        "nettype": "NETWORK_WIFI",
        "udid": "0",
    }

    @classmethod
    def _request(cls, method: str, module: str, param: dict) -> dict:
        payload = {
            "comm": dict(cls._COMM),
            "request": {
                "method": method,
                "module": module,
                "param": param,
            },
        }
        resp = requests.post(
            cls._MUSICU_URL, json=payload, headers=cls._HEADERS, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"QQ API outer error: {data.get('code')}")
        req = data.get("request", {})
        if req.get("code") != 0:
            raise RuntimeError(f"QQ API request error: {req.get('code')}")

        return req.get("data", {})

    @classmethod
    def search_song(cls, keyword: str, page: int = 1, limit: int = 10) -> List[Dict]:
        """
        搜索歌曲，返回歌曲列表
        """
        keyword = re.sub(r"[|!@#$%^&*/]+", "", keyword)
        param = {
            "search_id": "",
            "remoteplace": "search.android.keyboard",
            "query": keyword,
            "search_type": 0,
            "num_per_page": limit,
            "page_num": page,
            "highlight": 0,
            "nqc_flag": 0,
            "page_id": 1,
            "grp": 1,
        }
        try:
            data = cls._request(
                "DoSearchForQQMusicDesktop", "music.search.SearchCgiService", param
            )
        except Exception as e:
            logger.warning(f"QQ音乐搜索请求失败: {e}")
            return []

        song_list = data.get("body", {}).get("song", {}).get("list", [])
        if not song_list:
            return []

        results = []
        for item in song_list:
            duration = item.get("interval", 0)
            singers = [s.get("name", "") for s in item.get("singer", [])]
            album_info = item.get("album", {})
            album_name = album_info.get("name", "") if album_info else ""
            song_mid = item.get("mid", "")
            song_id = item.get("id", "")
            results.append(
                {
                    "idOrMd5": song_mid or str(song_id),
                    "songName": item.get("title", "") or item.get("name", ""),
                    "singer": ",".join(singers),
                    "album": album_name,
                    "duration": f"{duration // 60}:{duration % 60 // 10}{duration % 60}",
                    "_song_id": song_id,
                    "_song_mid": song_mid,
                }
            )
        return results

    @classmethod
    def get_song_info(cls, song_mid: str) -> Optional[Dict]:
        """
        根据歌曲 MID 获取详细信息
        """
        try:
            param = {"song_mid": song_mid}
            data = cls._request(
                "get_song_detail_yqq", "music.pf_song_detail_svr", param
            )
        except Exception as e:
            logger.warning(f"QQ音乐获取歌曲详情失败: {e}")
            return None

        track = data.get("track_info", {})
        if not track:
            return None

        singers = [s.get("name", "") for s in track.get("singer", [])]
        album = track.get("album", {})
        album_name = album.get("name", "") or album.get("title", "")
        album_mid = album.get("mid", "") or album.get("pmid", "")
        duration = track.get("interval", 0)
        song_id = track.get("id", 0)

        year = ""
        time_public = track.get("time_public", "")
        if time_public:
            year_match = re.search(r"(\d{4})", time_public)
            if year_match:
                year = year_match.group(1)

        track_number = track.get("index_album", 0)
        album_song_count = 0

        pic_buffer = io.BytesIO()
        if album_mid:
            pic_url = (
                f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"
            )
            try:
                pic_response = requests.get(pic_url, timeout=10)
                if pic_response.status_code == 200 and len(pic_response.content) > 1000:
                    with Image.open(io.BytesIO(pic_response.content)) as img:
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        img.thumbnail((500, 500))
                        pic_buffer = io.BytesIO()
                        img.save(pic_buffer, format="JPEG", quality=85)
                        pic_buffer.seek(0)
            except Exception as e:
                logger.warning(f"QQ音乐获取封面失败: {e}")

        lyric = cls.get_lyrics_by_song_mid(
            song_mid, song_id, track.get("name", ""), singers, album_name, duration
        )
        return {
            "singer": ",".join(singers),
            "songName": track.get("name", "") or track.get("title", ""),
            "album": album_name,
            "year": year,
            "trackNumber": (track_number, album_song_count),
            "duration": f"{duration // 60}:{duration % 60 // 10}{duration % 60}",
            "picBuffer": pic_buffer,
            "lyric": lyric,
        }

    @classmethod
    def get_lyrics_by_song_mid(
        cls,
        song_mid: str,
        song_id: int = 0,
        song_name: str = "",
        singers: Optional[list] = None,
        album_name: str = "",
        duration: int = 0,
    ) -> str:
        """
        根据歌曲信息获取歌词
        使用 music.musichallSong.PlayLyricInfo 接口
        """
        singers = singers or []
        singer_str = "、".join(singers)

        album_name_b64 = (
            base64.b64encode(album_name.encode("utf-8")).decode("ascii")
            if album_name
            else ""
        )
        singer_name_b64 = (
            base64.b64encode(singer_str.encode("utf-8")).decode("ascii")
            if singer_str
            else ""
        )
        song_name_b64 = (
            base64.b64encode(song_name.encode("utf-8")).decode("ascii")
            if song_name
            else ""
        )

        param = {
            "albumName": album_name_b64,
            "crypt": 1,
            "ct": 19,
            "cv": 2111,
            "interval": duration,
            "lrc_t": 0,
            "qrc": 1,
            "qrc_t": 0,
            "roma": 0,
            "roma_t": 0,
            "singerName": singer_name_b64,
            "songID": song_id,
            "songMId": song_mid,
            "songName": song_name_b64,
            "trans": 1,
            "trans_t": 0,
            "type": 0,
        }
        try:
            data = cls._request(
                "GetPlayLyricInfo", "music.musichallSong.PlayLyricInfo", param
            )
        except Exception as e:
            logger.warning(f"QQ音乐获取歌词失败: {e}")
            return ""

        lrc_text = data.get("lyric", "")
        qrc_t = data.get("qrc_t", "0")
        lrc_t = data.get("lrc_t", "0")

        original_lyric = ""
        if lrc_text and (qrc_t != "0" or lrc_t != "0"):
            decrypted = qrc_decrypt(lrc_text)
            if decrypted:
                original_lyric = cls._qrc_to_lrc(decrypted)

        trans_text = data.get("trans", "")
        trans_t = data.get("trans_t", "0")

        translation_lyric = ""
        if trans_text and trans_t != "0":
            decrypted_trans = qrc_decrypt(trans_text)
            if decrypted_trans:
                translation_lyric = cls._qrc_to_lrc(decrypted_trans)
        if original_lyric and translation_lyric:
            return cls._merge_lyric_with_translation(original_lyric, translation_lyric)
        return original_lyric

    @staticmethod
    def _qrc_to_lrc(qrc: str) -> str:
        """
        将 QRC 格式歌词转换为标准 LRC 格式
        支持括号式逐字时间戳 (offset,duration) 和角括号式 <start,duration,*>word
        """
        core = QQMusicApi._extract_lyric_content_if_xml(qrc)

        has_word_timestamps_paren = bool(re.search(r"\(\d+,\d+(?:,\d+)?\)", core))
        has_word_timestamps_angle = bool(re.search(r"<(?:\d+),(?:\d+),\d+>", core))
        contains_lrc_timestamps = bool(re.search(r"\[(\d+):(\d+)\.(\d+)\]", core))

        if has_word_timestamps_paren:
            return QQMusicApi._qrc_paren_to_lrc(core).strip()
        elif has_word_timestamps_angle:
            return QQMusicApi._qrc_angle_to_lrc(core).strip()
        elif contains_lrc_timestamps:
            return core.strip()
        else:
            return QQMusicApi._qrc_angle_to_lrc(core).strip()

    @staticmethod
    def _extract_lyric_content_if_xml(raw: str) -> str:
        if raw.startswith("<?xml") or "<QrcInfos" in raw:
            m = re.search(r'LyricContent="(.*?)"', raw, re.DOTALL)
            if m:
                return m.group(1).replace("\r\n", "\n").replace("\r", "\n").strip()
        return raw

    @staticmethod
    def _format_ms(ms: int) -> str:
        minutes = ms // 60000
        seconds = (ms % 60000) // 1000
        millis = ms % 1000
        return f"[{minutes:02d}:{seconds:02d}.{millis:03d}]"

    @staticmethod
    def _qrc_paren_to_lrc(qrc: str) -> str:
        """
        处理括号式逐字时间戳: (offset,duration[,extra]) 紧随词后
        """
        result_lines = []
        for line in qrc.split("\n"):
            line = line.strip()
            if not line:
                continue
            if re.match(r"^\[\w+:.*\]$", line):
                result_lines.append(line)
                continue

            start_ms = None
            content = line

            lrc_match = re.match(r"^\[(\d+):(\d+)\.(\d+)\](.*)$", line)
            if lrc_match:
                m = lrc_match
                start_ms = (
                    int(m.group(1)) * 60000
                    + int(m.group(2)) * 1000
                    + int(m.group(3)) * 10
                )
                content = m.group(4)
            else:
                qrc_match = re.match(r"^\[(\d+),(\d+)\](.*)$", line)
                if qrc_match:
                    start_ms = int(qrc_match.group(1))
                    content = qrc_match.group(3)

            if start_ms is None:
                result_lines.append(line)
                continue

            result_lines.append(QQMusicApi._format_ms(start_ms))

            tag_pattern = re.compile(r"\((\d+),(?:\d+)(?:,\d+)?\)")
            last = 0
            for tag in tag_pattern.finditer(content):
                word_text = content[last : tag.start()]
                word_offset_abs = int(tag.group(1))
                result_lines[-1] += (
                    f"<{QQMusicApi._format_ms(word_offset_abs)[1:-1]}>{word_text}"
                )
                last = tag.end()
            tail = content[last:]
            if tail:
                result_lines[-1] += tail

            result_lines[-1] = result_lines[-1]

        return "\n".join(result_lines)

    @staticmethod
    def _qrc_angle_to_lrc(qrc: str) -> str:
        """
        处理角括号式逐字时间戳: <start,duration,*>word
        """
        result_lines = []
        for line in qrc.split("\n"):
            line = line.strip()
            if not line or not line.startswith("["):
                continue

            if re.match(r"^\[\w+:.*\]$", line):
                result_lines.append(line)
                continue

            line_match = re.match(r"^\[(\d+),(\d+)\](.*)$", line)
            if line_match:
                start_ms = int(line_match.group(1))
                content = line_match.group(3)

                lrc_line = QQMusicApi._format_ms(start_ms)

                word_pattern = re.compile(
                    r"(?:\[\d+,\d+\])?<(\d+),(\d+),\d+>(.*?)(?=<\d+,\d+,\d+>|$)"
                )
                has_words = False
                for word_match in word_pattern.finditer(content):
                    word_start_ms = start_ms + int(word_match.group(1))
                    word_text = word_match.group(3)
                    lrc_line += (
                        f"<{QQMusicApi._format_ms(word_start_ms)[1:-1]}>{word_text}"
                    )
                    has_words = True

                if not has_words:
                    lrc_line += content

                result_lines.append(lrc_line)

        return "\n".join(result_lines)

    @staticmethod
    def _merge_lyric_with_translation(lrc_text: str, trans_text: str) -> str:
        """
        合并原歌词和翻译歌词
        """

        def parse_lyric_lines(text: str) -> tuple:
            lyric_dict = {}
            no_timestamp_lines = []
            timestamp_pattern = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")
            for line in text.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                match = timestamp_pattern.match(line)
                if match:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    ms = int(match.group(3).ljust(3, "0")[:3])
                    timestamp = minutes * 60000 + seconds * 1000 + ms
                    lyric_content = match.group(4).strip()
                    if timestamp not in lyric_dict:
                        lyric_dict[timestamp] = {"original": "", "translation": ""}
                    lyric_dict[timestamp]["original"] = lyric_content
                else:
                    no_timestamp_lines.append(line)
            return lyric_dict, no_timestamp_lines

        def format_timestamp(ms: int) -> str:
            minutes = ms // 60000
            seconds = (ms % 60000) // 1000
            millis = ms % 1000
            return f"[{minutes:02d}:{seconds:02d}.{millis:03d}]"

        lrc_dict, lrc_no_ts = parse_lyric_lines(lrc_text)
        trans_dict, _ = parse_lyric_lines(trans_text)

        for timestamp, content in trans_dict.items():
            if timestamp in lrc_dict and content["original"]:
                lrc_dict[timestamp]["translation"] = content["original"]

        result_lines = lrc_no_ts[:]
        for timestamp in sorted(lrc_dict.keys()):
            content = lrc_dict[timestamp]
            if content["original"]:
                result_lines.append(
                    f"{format_timestamp(timestamp)}{content['original']}"
                )
            if content["translation"]:
                result_lines.append(
                    f"{format_timestamp(timestamp)}{content['translation']}"
                )

        return "\n".join(result_lines)

    @classmethod
    def search_artist(cls, keyword: str, limit: int = 10) -> List[Dict]:
        """
        搜索歌手
        """
        param = {
            "search_id": "",
            "remoteplace": "search.android.keyboard",
            "query": keyword,
            "search_type": 1,
            "num_per_page": limit,
            "page_num": 1,
            "highlight": 0,
            "nqc_flag": 0,
            "page_id": 1,
            "grp": 1,
        }
        try:
            data = cls._request(
                "DoSearchForQQMusicDesktop", "music.search.SearchCgiService", param
            )
        except Exception as e:
            logger.warning(f"QQ音乐搜索歌手失败: {e}")
            return []

        singer_list = data.get("body", {}).get("singer", {}).get("list", [])
        results = []
        for item in singer_list:
            singer_mid = item.get("mid", "") or item.get("singerMID", "") or item.get("singermid", "")
            pic_url = ""
            singer_pic = item.get("singerPic", "")
            # if singer_mid:
            #     pic_url = f"https://y.gtimg.cn/music/photo_new/T001R300x300M000{singer_mid}.jpg"
            singer_mid = item.get("mid", "") or item.get("singerMID", "") or item.get("singermid", "")
            pic_url = ""
            singer_pic = item.get("singerPic", "")
            if singer_pic:
                pic_url = f"{singer_pic}"
            if singer_mid:
                pic_url = f"https://y.gtimg.cn/music/photo_new/T001R300x300M000{singer_mid}.jpg"
            results.append(
                {
                    "id": item.get("id", "") or item.get("singerid", ""),
                    "mid": singer_mid,
                    "name": item.get("name", "") or item.get("singername", "") or item.get("singerName", ""),
                    "picUrl": pic_url,
                }
            )
        return results

    @classmethod
    def get_artist_avatar_url(cls, artist_name: str) -> Optional[str]:
        """
        获取歌手头像 URL
        """
        try:
            search_results = cls.search_artist(artist_name, limit=1)
            if search_results:
                pic_url = search_results[0].get("picUrl", "")
                if pic_url:
                    return pic_url
            return None
        except Exception as e:
            logger.warning(f"QQ音乐获取歌手头像URL失败: {e}")
            return None

    @classmethod
    def download_artist_avatar(cls, artist_name: str) -> Optional[bytes]:
        """
        下载歌手头像图片
        """
        pic_url = cls.get_artist_avatar_url(artist_name)
        if not pic_url:
            return None

        try:
            resp = requests.get(pic_url, headers=cls._HEADERS, timeout=15)
            resp.raise_for_status()
            if len(resp.content) < 1000:
                return None
            return resp.content
        except Exception as e:
            logger.warning(f"QQ音乐下载歌手头像失败: {e}")
            return None
