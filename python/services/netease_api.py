"""
网易云音乐 API 模块
集成搜索、歌曲信息、歌词、歌手头像等功能
"""

import re
import io
import time
import json
import logging
from typing import List, Dict, Optional, Any
from hashlib import md5
from urllib.parse import urlparse
from PIL import Image
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger("tunetree")


class APIConstants:
    AES_KEY = b"e82ckenh8dichen8"
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/2.10.2.200154'
    REFERER = 'https://music.163.com/'
    SEARCH_API = 'https://music.163.com/api/cloudsearch/pc'
    DEFAULT_COOKIES = {
        "os": "pc",
        "appver": "",
        "osver": "",
        "deviceId": "pyncm!"
    }


class CryptoUtils:
    @staticmethod
    def hex_digest(data: bytes) -> str:
        return "".join([hex(d)[2:].zfill(2) for d in data])
    
    @staticmethod
    def hash_digest(text: str) -> bytes:
        return md5(text.encode("utf-8")).digest()
    
    @staticmethod
    def hash_hex_digest(text: str) -> str:
        return CryptoUtils.hex_digest(CryptoUtils.hash_digest(text))
    
    @staticmethod
    def encrypt_params(url: str, payload: Dict[str, Any]) -> str:
        url_path = urlparse(url).path.replace("/eapi/", "/api/")
        digest = CryptoUtils.hash_hex_digest(f"nobody{url_path}use{json.dumps(payload)}md5forencrypt")
        params = f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
        
        padder = padding.PKCS7(algorithms.AES(APIConstants.AES_KEY).block_size).padder()
        padded_data = padder.update(params.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(APIConstants.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        enc = encryptor.update(padded_data) + encryptor.finalize()
        
        return CryptoUtils.hex_digest(enc)


class NeteaseApi:
    """
    网易云音乐 API 客户端
    """
    
    _session = None
    
    @classmethod
    def _get_session(cls):
        if cls._session is None:
            cls._session = requests.Session()
        return cls._session
    
    @classmethod
    def _reset_session(cls):
        try:
            if cls._session:
                cls._session.close()
        except:
            pass
        cls._session = requests.Session()

    # ==================== 歌曲搜索和信息 ====================
    
    @classmethod
    def search_song(cls, keyword: str, page: int = 0, limit: int = 20) -> List[Dict]:
        """
        搜索歌曲，返回歌曲列表
        """
        search_url = 'https://music.163.com/api/search/get/web?&s={}&type=1&offset={}&total=true&limit=20'
        keyword = re.sub(r"|[!@#$%^&*/]+", "", keyword)
        res_json = requests.post(search_url.format(keyword, page * 20), timeout=10).json()
        res_list = []
        if res_json["result"] == {} or res_json['code'] == 400 or res_json["result"]["songCount"] == 0:
            return res_list
        for data in res_json["result"]["songs"]:
            duration = data["duration"] // 1000
            artists_list = [info["name"] for info in data["artists"]]
            res_list.append({
                "idOrMd5": str(data['id']),
                "songName": data['name'],
                "singer": ','.join(artists_list),
                "duration": f'{duration // 60}:{duration % 60 // 10}{duration % 60}'
            })
        return res_list

    @classmethod
    def get_song_info(cls, song_id: str) -> Optional[Dict]:
        """
        根据歌曲 ID 获取详细信息
        """
        song_info_url = 'http://music.163.com/api/song/detail/?id={}&ids=[{}]'
        res_json = requests.post(song_info_url.format(song_id, song_id), timeout=10).json()
        if res_json['code'] == 400 or res_json['code'] == 406:
            raise requests.RequestException("访问过于频繁或接口失效")
        song_json = res_json['songs'][0]
        artists_list = [info["name"] for info in song_json["artists"]]
        duration = song_json["duration"] // 1000

        pic_url = song_json["album"]["picUrl"]
        pic_response = requests.get(pic_url, timeout=10)
        pic_response.raise_for_status()

        with Image.open(io.BytesIO(pic_response.content)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.thumbnail((500, 500))
            pic_buffer = io.BytesIO()
            img.save(pic_buffer, format='JPEG', quality=85)
            pic_buffer.seek(0)

        lyric = ""
        try:
            lrc_url = 'http://music.163.com/api/song/lyric?id={}&lv=-1&kv=-1&tv=-1&rv=-1'
            lrc_json = requests.get(lrc_url.format(song_id), timeout=10).json()
            if lrc_json.get('lrc', {}).get('lyric'):
                lyric = lrc_json['lrc']['lyric']
        except Exception as e:
            logger.warning(f"获取歌词失败: {e}")

        return {
            "singer": ','.join(artists_list),
            "songName": song_json["name"],
            "album": song_json["album"]["name"],
            "year": str(time.localtime(song_json["album"]["publishTime"] // 1000).tm_year),
            "trackNumber": (song_json["no"], song_json["album"]["size"]),
            "duration": f'{duration // 60}:{duration % 60 // 10}{duration % 60}',
            "picBuffer": pic_buffer,
            "lyric": lyric
        }

    # ==================== 歌手头像 ====================
    
    @classmethod
    def search_artist(cls, keyword: str, limit: int = 10) -> List[Dict]:
        """
        搜索歌手
        """
        try:
            data = {'s': keyword, 'type': 100, 'limit': limit}
            headers = {'User-Agent': APIConstants.USER_AGENT, 'Referer': APIConstants.REFERER}
            
            session = cls._get_session()
            response = session.post(
                APIConstants.SEARCH_API, 
                data=data, 
                headers=headers, 
                cookies=APIConstants.DEFAULT_COOKIES, 
                timeout=15
            )
            response.raise_for_status()
            
            result = response.json()
            if result.get('code') != 200:
                logger.warning(f"网易云搜索失败: {result.get('message', '未知错误')}")
                return []
            
            results = []
            for item in result.get('result', {}).get('artists', []):
                results.append({
                    'id': item['id'],
                    'name': item['name'],
                    'picUrl': item.get('picUrl', '') or item.get('img1v1Url', '')
                })
            return results
        except Exception as e:
            logger.error(f"搜索歌手失败: {e}")
            return []
    
    @classmethod
    def get_artist_avatar_url(cls, artist_name: str) -> Optional[str]:
        """
        获取歌手头像 URL
        """
        try:
            search_results = cls.search_artist(artist_name, limit=1)
            if search_results:
                pic_url = search_results[0].get('picUrl', '')
                if pic_url:
                    pic_url = pic_url.replace('http://', 'https://')
                    if '?' not in pic_url:
                        pic_url += '?param=300y300'
                    return pic_url
            return None
        except Exception as e:
            logger.error(f"获取歌手头像 URL 失败: {e}")
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
            session = cls._get_session()
            response = session.get(pic_url, timeout=15)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"下载歌手头像失败: {e}")
            return None
