"""
酷狗音乐 API 模块
"""

import io
import logging
import threading
from typing import List, Dict, Optional
from PIL import Image
import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger("tunetree")


_HTTP_SESSION_LOCAL = threading.local()


def _get_http_session() -> requests.Session:
    """获取当前线程复用的 requests.Session（连接池 + 少量重试）。

    Session 非线程安全，按线程持有以避免并发外呼时的状态竞争。
    """
    session = getattr(_HTTP_SESSION_LOCAL, "session", None)
    if session is None:
        from urllib3.util.retry import Retry

        retry = Retry(
            total=1,
            connect=1,
            read=1,
            backoff_factor=0.2,
            status_forcelist=(502, 503, 504),
            allowed_methods=["GET", "POST"],
        )
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _HTTP_SESSION_LOCAL.session = session
    return session


class KugouApi:
    """
    酷狗音乐 API 客户端
    """
    
    def __init__(self):
        self._get_hash_search_url = 'http://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword={}&page={}&pagesize=20&showtype=1'
        self._get_key_search_url = 'http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword=&duration=&hash={}'
        self._get_lrc_url = 'http://lyrics.kugou.com/download?ver=1&client=pc&id={}&accesskey={}&fmt={}&charset=utf8'
        self._album_info_url = 'http://mobilecdn.kugou.com/api/v3/album/info?albumid={}&plat=0&pagesize=100&area_code=1'
        self._song_info_url = 'http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash={}'
        self._header = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:7.0a1) Gecko/20110623 Firefox/7.0a1 Fennec/7.0a1'}

    @classmethod
    def search_hash(cls, keyword: str, page: int = 1) -> List[Dict]:
        """
        搜索歌曲，返回歌曲哈希列表
        """
        keyword = keyword.replace("|", "").replace("!", "").replace("@", "").replace("#", "").replace("$", "").replace("%", "").replace("^", "").replace("&", "").replace("*", "").replace("/", "").replace("+", "")
        search_url = 'http://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword={}&page={}&pagesize=10&showtype=1'
        res_json = _get_http_session().get(search_url.format(keyword, page), headers={'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:7.0a1) Gecko/20110623 Firefox/7.0a1 Fennec/7.0a1'}, timeout=10).json()
        song_info_list = []
        if 'data' not in res_json or 'info' not in res_json['data']:
            return song_info_list
        for data in res_json['data']['info']:
            duration = data["duration"]
            song_info_list.append({
                "idOrMd5": data["hash"],
                "songName": data["songname"],
                "singer": data["singername"],
                "duration": f'{duration // 60}:{duration % 60 // 10}{duration % 60}'
            })
        return song_info_list

    @classmethod
    def get_song_info(cls, hash: str) -> Optional[Dict]:
        """
        根据歌曲哈希获取详细信息
        """
        song_info_url = 'http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash={}'
        album_info_url = 'http://mobilecdn.kugou.com/api/v3/album/info?albumid={}&plat=0&pagesize=10&area_code=1'
        header = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:7.0a1) Gecko/20110623 Firefox/7.0a1 Fennec/7.0a1'}
        song_json = _get_http_session().get(song_info_url.format(hash), headers=header, timeout=10).json()
        if(song_json.get("errcode") == 1002):
            return {
            "errcode": song_json.get("errcode", "")
            }
        duration = song_json.get("timeLength", 0)
        album_img = song_json.get("album_img", "")

        album_id = song_json.get("albumid", 0)
        album = None
        year = None
        if album_id and album_id != 0:
            try:
                album_json = _get_http_session().get(album_info_url.format(album_id), headers=header, timeout=10).json()
                if 'data' in album_json:
                    album = album_json["data"].get("albumname")
                    year = album_json["data"].get("publishtime")
            except Exception as e:
                logger.warning(f"获取专辑信息失败: {e}")

        pic_buffer = io.BytesIO()
        pic_url = album_img.replace("/{size}/", "/")
        if pic_url:
            try:
                pic_response = _get_http_session().get(pic_url, timeout=10)
                pic_response.raise_for_status()
                with Image.open(io.BytesIO(pic_response.content)) as img:
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.thumbnail((500, 500))
                    pic_buffer = io.BytesIO()
                    img.save(pic_buffer, format='JPEG', quality=85)
                    pic_buffer.seek(0)
            except Exception as e:
                logger.warning(f"获取封面失败: {e}")
        return {
            "singer": song_json.get("author_name", ""),
            "songName": song_json.get("songName", ""),
            "album": album,
            "year": year,
            "duration": f'{duration // 60}:{duration % 60 // 10}{duration % 60}' if duration else None,
            "picBuffer": pic_buffer
        }
