"""
元数据刮削服务模块
统一管理不同数据源的 API 调用
"""

import re
import logging
import base64
from typing import List, Dict, Optional
from services.netease_api import NeteaseApi
from services.kugou_api import KugouApi

logger = logging.getLogger("tunetree")


class MetadataScraper:
    """
    元数据刮削器，整合多个数据源
    """
    
    def __init__(self):
        self.api_order = ["kugou", "cloud"]

    def _build_search_keywords(self, filename: str, current_meta: Dict) -> List[str]:
        """
        构建搜索关键词列表
        """
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
        """
        刮削元数据
        """
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
        """
        使用指定的 API 刮削
        """
        for keyword in keywords:
            try:
                search_results = []
                if api_name == "cloud":
                    search_results = NeteaseApi.search_song(keyword)
                elif api_name == "kugou":
                    search_results = KugouApi.search_hash(keyword)
                if search_results:
                    song_info = None
                    if api_name == "cloud":
                        song_info = NeteaseApi.get_song_info(search_results[0]["idOrMd5"])
                    elif api_name == "kugou":
                        song_info = KugouApi.get_song_info(search_results[0]["idOrMd5"])
                    if song_info:
                        return self._song_info_to_dict(song_info, api_name)
            except Exception as e:
                logger.warning(f"关键词 '{keyword}' 通过 {api_name} 搜索失败: {e}")
                continue
        return None

    def _song_info_to_dict(self, song_info: Dict, source: str) -> Dict:
        """
        歌曲信息转换为字典
        """
        result = {
            "title": song_info["songName"],
            "artist": song_info["singer"],
            "album": song_info["album"],
            "album_artist": song_info["singer"],
            "year": song_info["year"],
            "track_num": song_info["trackNumber"][0] if song_info.get("trackNumber") else None,
            "lyrics": song_info.get("lyric"),
            "_source": source,
            "_has_cover": song_info.get("picBuffer") is not None and song_info.get("picBuffer").getvalue() != b'',
        }

        if song_info.get("picBuffer") and song_info.get("picBuffer").getvalue():
            result["_cover_data"] = base64.b64encode(song_info.get("picBuffer").getvalue()).decode()

        return result

    def search_all_apis(self, filename: str, current_meta: Dict, max_per_api: int = 3) -> Dict[str, List[Dict]]:
        """
        批量搜索所有 API
        """
        keywords = self._build_search_keywords(filename, current_meta)
        logger.info(f"批量刮削开始，关键词: {keywords}")

        results = {
            "cloud": [],
            "kugou": []
        }

        for api_name in self.api_order:
            logger.info(f"正在搜索 {api_name} API...")
            try:
                api_results = self._search_api_with_multiple_results(api_name, keywords, max_per_api)
                results[api_name] = api_results
                logger.info(f"{api_name} API 返回 {len(api_results)} 条结果")
            except Exception as e:
                logger.warning(f"{api_name} API 批量搜索失败: {e}")
                results[api_name] = []

        total = sum(len(v) for v in results.values())
        logger.info(f"批量刮削完成，共返回 {total} 条结果")

        return results

    def _search_api_with_multiple_results(self, api_name: str, keywords: List[str], max_results: int) -> List[Dict]:
        """
        搜索并返回多个结果
        """
        all_results = []

        for keyword in keywords:
            if len(all_results) >= max_results:
                break

            try:
                search_results = []
                if api_name == "cloud":
                    search_results = NeteaseApi.search_song(keyword)
                elif api_name == "kugou":
                    search_results = KugouApi.search_hash(keyword)

                for search_result in search_results[:max_results]:
                    if len(all_results) >= max_results:
                        break

                    try:
                        song_info = None
                        if api_name == "cloud":
                            song_info = NeteaseApi.get_song_info(search_result["idOrMd5"])
                        elif api_name == "kugou":
                            song_info = KugouApi.get_song_info(search_result["idOrMd5"])
                        if song_info:
                            result_dict = self._song_info_to_dict(song_info, api_name)
                            result_dict["_search_keyword"] = keyword
                            all_results.append(result_dict)
                    except Exception as e:
                        logger.warning(f"获取 {api_name} 歌曲详情失败: {e}")
                        continue
            except Exception as e:
                logger.warning(f"关键词 '{keyword}' 通过 {api_name} 搜索失败: {e}")
                continue

        return all_results
