#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
元数据刮削服务模块
集成了网易云音乐、酷狗、Spotify 三个数据源
"""

import re
import io
import time
import base64
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from PIL import Image
import requests

logger = logging.getLogger("tunetree")


@dataclass
class SongSearchInfo:
    songName: str
    singer: str
    duration: str
    idOrMd5: str


@dataclass
class SongInfo:
    singer: str
    songName: str
    album: Optional[str] = None
    year: Optional[str] = None
    trackNumber: Optional[Tuple[int, Optional[int]]] = None
    duration: Optional[str] = None
    genre: Optional[str] = None
    picBuffer: Optional[io.BytesIO] = None
    lyric: Optional[str] = None


class NeteaseCloudMusicWebApi:
    def __init__(self):
        self._search_url = 'https://music.163.com/api/search/get/web?&s={}&type=1&offset={}&total=true&limit=20'
        self._song_info_url = 'http://music.163.com/api/song/detail/?id={}&ids=[{}]'
        self._download_lrc_url = 'http://music.163.com/api/song/lyric?id={}&lv=-1&kv=-1&tv=-1&rv=-1'

    def get_song_info(self, song_id: str) -> SongInfo:
        res_json = requests.post(self._song_info_url.format(song_id, song_id), timeout=10).json()
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
            lrc_json = requests.get(self._download_lrc_url.format(song_id), timeout=10).json()
            if lrc_json.get('lrc', {}).get('lyric'):
                lyric = lrc_json['lrc']['lyric']
        except Exception as e:
            logger.warning(f"获取歌词失败: {e}")

        return SongInfo(
            singer=','.join(artists_list),
            songName=song_json["name"],
            album=song_json["album"]["name"],
            year=str(time.localtime(song_json["album"]["publishTime"] // 1000).tm_year),
            trackNumber=(song_json["no"], song_json["album"]["size"]),
            duration=f'{duration // 60}:{duration % 60 // 10}{duration % 60}',
            picBuffer=pic_buffer,
            lyric=lyric
        )

    def search_data(self, keyword: str, page: int = 0) -> List[SongSearchInfo]:
        keyword = re.sub(r"|[!@#$%^&*/]+", "", keyword)
        res_json = requests.post(self._search_url.format(keyword, page * 20), timeout=10).json()
        res_list = []
        if res_json["result"] == {} or res_json['code'] == 400 or res_json["result"]["songCount"] == 0:
            return res_list
        for data in res_json["result"]["songs"]:
            duration = data["duration"] // 1000
            artists_list = [info["name"] for info in data["artists"]]
            res_list.append(SongSearchInfo(
                idOrMd5=str(data['id']),
                songName=data['name'],
                singer=','.join(artists_list),
                duration='%d:%d%d' % (duration // 60, duration % 60 // 10, duration % 60)
            ))
        return res_list


class KugouApi:
    def __init__(self):
        self._get_hash_search_url = 'http://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword={}&page={}&pagesize=20&showtype=1'
        self._get_key_search_url = 'http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword=&duration=&hash={}'
        self._get_lrc_url = 'http://lyrics.kugou.com/download?ver=1&client=pc&id={}&accesskey={}&fmt={}&charset=utf8'
        self._album_info_url = 'http://mobilecdn.kugou.com/api/v3/album/info?albumid={}&plat=0&pagesize=100&area_code=1'
        self._song_info_url = 'http://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash={}'
        self._header = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:7.0a1) Gecko/20110623 Firefox/7.0a1 Fennec/7.0a1'}

    def search_hash(self, keyword: str, page: int = 1) -> List[SongSearchInfo]:
        keyword = re.sub(r"|[!@#$%^&*/]+", "", keyword)
        url = self._get_hash_search_url.format(keyword, page)
        res_json = requests.get(url, headers=self._header, timeout=10).json()
        song_info_list = []
        if 'data' not in res_json or 'info' not in res_json['data']:
            return song_info_list
        for data in res_json['data']['info']:
            duration = data["duration"]
            song_info_list.append(SongSearchInfo(
                singer=data["singername"],
                songName=data["songname"],
                duration=f'{duration // 60}:{duration % 60 // 10}{duration % 60}',
                idOrMd5=data["hash"]
            ))
        return song_info_list

    def get_song_info(self, md5: str) -> SongInfo:
        song_json = requests.get(self._song_info_url.format(md5), headers=self._header, timeout=10).json()
        duration = song_json.get("timeLength", 0)
        album_img = song_json.get("album_img", "")

        album_id = song_json.get("albumid", 0)
        album = None
        year = None
        if album_id and album_id != 0:
            try:
                album_json = requests.get(self._album_info_url.format(album_id), headers=self._header, timeout=10).json()
                if 'data' in album_json:
                    album = album_json["data"].get("albumname")
                    year = album_json["data"].get("publishtime")
            except Exception as e:
                logger.warning(f"获取专辑信息失败: {e}")

        pic_buffer = io.BytesIO()
        pic_url = album_img.replace("/{size}/", "/")
        if pic_url:
            try:
                pic_response = requests.get(pic_url, timeout=10)
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

        return SongInfo(
            singer=song_json.get("author_name", ""),
            songName=song_json.get("songName", ""),
            album=album,
            year=year,
            duration=f'{duration // 60}:{duration % 60 // 10}{duration % 60}' if duration else None,
            picBuffer=pic_buffer
        )


class SpotifyAuth:
    AUTH_TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_header = {'Authorization': 'Basic ' + base64.b64encode((client_id + ":" + client_secret).encode("ascii")).decode("ascii")}
        self.token_info = None

    def get_token(self):
        if self.token_info and self.token_info["expires_at"] > int(time.time()):
            return self.token_info["access_token"]
        self._fetch_access_token()
        return self.token_info["access_token"]

    def _fetch_access_token(self):
        payload = {"grant_type": "client_credentials"}
        response = requests.post(self.AUTH_TOKEN_URL, headers=self.auth_header, data=payload, timeout=10)
        response.raise_for_status()
        self.token_info = response.json()
        self.token_info["expires_at"] = int(time.time()) + self.token_info["expires_in"]


class SpotifyApi:
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self._client_id = client_id or "2fa0e4d172014c33809ff17e526d8559"
        self._client_secret = client_secret or "0e7d3cfe97794639966683001ca45eec"
        self.auth = SpotifyAuth(self._client_id, self._client_secret)
        self.header = {"Content-Type": "application/json"}

    def _get_auth_header(self) -> dict:
        return {"Authorization": "Bearer {0}".format(self.auth.get_token()), "Content-Type": "application/json"}

    def search_data(self, keyword: str, offset: int = 0, limit: int = 10) -> List[SongSearchInfo]:
        keyword = re.sub(r"|[!@#$%^&*/]+", "", keyword)
        params = {"query": keyword, "type": "track", "offset": offset, "limit": limit}
        res_json = requests.get(self.SEARCH_URL, headers=self._get_auth_header(), params=params, timeout=10).json()
        song_info_list = []
        if 'tracks' not in res_json or 'items' not in res_json['tracks']:
            return song_info_list
        for data in res_json['tracks']['items']:
            artists_list = [info["name"] for info in data["artists"]]
            duration = data["duration_ms"] // 1000
            song_info_list.append(SongSearchInfo(
                singer=','.join(artists_list),
                songName=data["name"],
                duration=f'{duration // 60}:{duration % 60 // 10}{duration % 60}',
                idOrMd5=data["id"]
            ))
        return song_info_list

    def get_song_info(self, song_id: str) -> SongInfo:
        url = "https://api.spotify.com/v1/tracks/{}".format(song_id)
        song_json = requests.get(url, headers=self._get_auth_header(), timeout=10).json()
        artists_list = [info["name"] for info in song_json["artists"]]
        duration = int(song_json["duration_ms"]) // 1000
        pic_url = song_json["album"]["images"][0]["url"] if song_json["album"]["images"] else ""
        pic_buffer = io.BytesIO()
        if pic_url:
            try:
                pic_data = requests.get(pic_url, timeout=10).content
                pic_buffer = io.BytesIO(pic_data)
            except Exception as e:
                logger.warning(f"获取Spotify封面失败: {e}")
        return SongInfo(
            singer=','.join(artists_list),
            songName=song_json["name"],
            album=song_json["album"]["name"],
            year=song_json["album"]["release_date"][:4] if song_json["album"]["release_date"] else None,
            trackNumber=(song_json["track_number"], None),
            duration=f'{duration // 60}:{duration % 60 // 10}{duration % 60}',
            picBuffer=pic_buffer
        )


class MetadataScraper:
    def __init__(self):
        self.cloud_api = NeteaseCloudMusicWebApi()
        self.kugou_api = KugouApi()
        self.spotify_api = SpotifyApi()
        self.api_order = ["kugou", "cloud", "spotify"]

    def _build_search_keywords(self, filename: str, current_meta: Dict) -> List[str]:
        keywords = []

        import os
        filename_no_ext = os.path.splitext(os.path.basename(filename))[0]

        # 先检查是否有元数据中的歌名
        has_meta_title = current_meta.get("title") and current_meta["title"].strip()
        has_meta_artist = current_meta.get("artist") and current_meta["artist"].strip()
        has_meta_album = current_meta.get("album") and current_meta["album"].strip()

        # 尝试从文件名解析 artist - title 格式
        parsed_artist = None
        parsed_title = None
        if not has_meta_title and not has_meta_artist:
            # 匹配 "artist - title" 格式（中间有空格-空格）
            match = re.match(r'^\s*([^-]+?)\s*-\s*(.+?)\s*$', filename_no_ext)
            if match:
                parsed_artist = match.group(1).strip()
                parsed_title = match.group(2).strip()
                # 确保解析出来的内容不为空
                if parsed_artist and parsed_title:
                    logger.info(f"从文件名解析: artist='{parsed_artist}', title='{parsed_title}'")

        # 确定歌名：优先元数据，其次解析结果，最后文件名
        title = None
        if has_meta_title:
            title = current_meta["title"]
        elif parsed_title:
            title = parsed_title
        else:
            title = filename_no_ext
        keywords.append(title)

        # 确定艺术家：优先元数据，其次解析结果
        if has_meta_artist:
            if current_meta["artist"] != title:
                keywords.append(current_meta["artist"])
        elif parsed_artist:
            if parsed_artist != title:
                keywords.append(parsed_artist)

        # 专辑：只有元数据中有才使用
        if has_meta_album:
            album = current_meta["album"]
            if album != title and album != (current_meta.get("artist") or parsed_artist):
                keywords.append(album)

        return keywords

    def scrape(self, filename: str, current_meta: Dict, preferred_api: Optional[str] = None) -> Optional[Dict]:
        keywords = self._build_search_keywords(filename, current_meta)
        logger.info(f"开始刮削元数据，关键词: {keywords}")

        api_list = [preferred_api] if preferred_api and preferred_api in self.api_order else self.api_order

        for api_name in api_list:
            logger.info(f"尝试使用 {api_name} API")
            try:
                result = self._scrape_by_api(api_name, keywords)
                if result:
                    logger.info(f"通过 {api_name} API 成功获取元数据")
                    return result
            except Exception as e:
                logger.warning(f"{api_name} API 调用失败: {e}")
                continue

        logger.warning("所有API均未找到匹配的元数据")
        return None

    def _scrape_by_api(self, api_name: str, keywords: List[str]) -> Optional[Dict]:
        for keyword in keywords:
            try:
                search_results = []
                if api_name == "cloud":
                    search_results = self.cloud_api.search_data(keyword)
                elif api_name == "kugou":
                    search_results = self.kugou_api.search_hash(keyword)
                elif api_name == "spotify":
                    search_results = self.spotify_api.search_data(keyword)

                if search_results:
                    song_info = None
                    if api_name == "cloud":
                        song_info = self.cloud_api.get_song_info(search_results[0].idOrMd5)
                    elif api_name == "kugou":
                        song_info = self.kugou_api.get_song_info(search_results[0].idOrMd5)
                    elif api_name == "spotify":
                        song_info = self.spotify_api.get_song_info(search_results[0].idOrMd5)

                    if song_info:
                        return self._song_info_to_dict(song_info, api_name)
            except Exception as e:
                logger.warning(f"关键词 '{keyword}' 通过 {api_name} 搜索失败: {e}")
                continue
        return None

    def _song_info_to_dict(self, song_info: SongInfo, source: str) -> Dict:
        result = {
            "title": song_info.songName,
            "artist": song_info.singer,
            "album": song_info.album,
            "album_artist": song_info.singer,
            "year": song_info.year,
            "track_num": song_info.trackNumber[0] if song_info.trackNumber else None,
            "lyrics": song_info.lyric,
            "_source": source,
            "_has_cover": song_info.picBuffer is not None and song_info.picBuffer.getvalue() != b'',
        }

        if song_info.picBuffer and song_info.picBuffer.getvalue():
            result["_cover_data"] = base64.b64encode(song_info.picBuffer.getvalue()).decode()

        return result
