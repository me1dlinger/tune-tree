"""
元数据刮削服务模块
统一管理不同数据源的 API 调用
"""

import re
import logging
import base64
import unicodedata
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from services.netease_api import NeteaseApi
from services.kugou_api import KugouApi

logger = logging.getLogger("tunetree")


def normalize_str(text: str) -> str:
    """对字符串进行Unicode正规化，用于比较"""
    if not text:
        return ""
    return unicodedata.normalize('NFKC', text).lower().strip()


class RateLimitException(Exception):
    """API 风控异常"""
    pass


class MetadataScraper:
    """
    元数据刮削器，整合多个数据源
    """

    TITLE_WEIGHT = 10
    ARTIST_WEIGHT = 8
    ALBUM_WEIGHT = 6

    def __init__(self):
        self.api_order = ["cloud", "kugou"]
        self._kugou_rate_limited = False

    def _calculate_match_score(self,  result: Dict, keywords: List[str]) -> float:
        
        """
        计算搜索结果与关键词的匹配分数

        评分规则：
        - 歌名精确匹配：10分
        - 歌名包含匹配：5分
        - 艺术家精确匹配：8分
        - 艺术家包含匹配：4分
        - 专辑精确匹配：6分
        - 专辑包含匹配：3分
        """
        score = 0.0

        title_keyword = keywords[0] if keywords else ""
        artist_keyword = keywords[1] if len(keywords) > 1 else ""
        album_keyword = keywords[2] if len(keywords) > 2 else ""
        result_title = normalize_str(result.get("title", ""))
        result_artist = normalize_str(result.get("artist", ""))
        result_album = normalize_str(result.get("album", ""))
        if title_keyword:
            title_kw_norm = normalize_str(title_keyword)
            if result_title == title_kw_norm:
                score += self.TITLE_WEIGHT
            elif title_kw_norm in result_title or result_title in title_kw_norm:
                score += self.TITLE_WEIGHT * 0.5

        if artist_keyword:
            artist_kw_norm = normalize_str(artist_keyword)
            if result_artist == artist_kw_norm:
                score += self.ARTIST_WEIGHT
            elif artist_kw_norm in result_artist or result_artist in artist_kw_norm:
                score += self.ARTIST_WEIGHT * 0.5

        if album_keyword:
            album_kw_norm = normalize_str(album_keyword)
            if result_album == album_kw_norm:
                score += self.ALBUM_WEIGHT
            elif album_kw_norm in result_album or result_album in album_kw_norm:
                score += self.ALBUM_WEIGHT * 0.5

        result["_match_score"] = score
        return score

    def _build_search_keywords(self, filename: str, current_meta: Dict, user_input: Dict = None) -> List[str]:
        """
        构建搜索关键词列表
        
        优先级：
        1. 用户输入（user_input）- 最高优先级
        2. 元数据标签（current_meta）
        3. 文件名解析
        4. 目录路径解析
        """
        keywords = []
        user_input = user_input or {}

        import os
        filename_no_ext = os.path.splitext(os.path.basename(filename))[0]

        # 检查用户输入
        has_user_title = user_input.get("title") and user_input["title"].strip()
        has_user_artist = user_input.get("artist") and user_input["artist"].strip()
        has_user_album = user_input.get("album") and user_input["album"].strip()

        # 检查元数据
        has_meta_title = current_meta.get("title") and current_meta["title"].strip()
        has_meta_artist = current_meta.get("artist") and current_meta["artist"].strip()
        has_meta_album = current_meta.get("album") and current_meta["album"].strip()

        # 尝试从文件名解析 artist - title 格式（仅当没有用户输入和元数据时）
        parsed_artist = None
        parsed_title = None
        if not has_user_title and not has_user_artist and not has_meta_title and not has_meta_artist:
            # 匹配 "artist - title" 格式（中间有空格-空格）
            match = re.match(r'^\s*([^-]+?)\s*-\s*(.+?)\s*$', filename_no_ext)
            if match:
                parsed_artist = match.group(1).strip()
                parsed_title = match.group(2).strip()
                # 确保解析出来的内容不为空
                if parsed_artist and parsed_title:
                    logger.info(f"从文件名解析: artist='{parsed_artist}', title='{parsed_title}'")

        # 确定歌名：优先用户输入，其次元数据，然后解析结果，最后文件名
        title = None
        if has_user_title:
            title = user_input["title"]
        elif has_meta_title:
            title = current_meta["title"]
        elif parsed_title:
            title = parsed_title
        else:
            # 去除音轨号前缀（如 "06. " 或 "06 - "）
            cleaned_title = re.sub(r'^\d+\s*[.-]\s*', '', filename_no_ext)
            title = cleaned_title if cleaned_title else filename_no_ext
        keywords.append(title)

        # 确定艺术家：优先用户输入，其次元数据，然后解析结果，最后从目录路径提取
        artist_from_path = None
        album_from_path = None
        
        if not has_user_artist and not has_user_album and not has_meta_artist and not has_meta_album and not parsed_artist:
            # 从目录路径提取艺术家和专辑（仅当没有用户输入和元数据时）
            try:
                relative_path = os.path.dirname(filename)
                # 获取路径组件
                path_parts = relative_path.replace('\\', '/').strip('/').split('/')
                # 过滤空组件
                path_parts = [p for p in path_parts if p and p.strip()]
                
                # 如果有至少2个目录层级，认为第一个是艺术家，第二个是专辑
                if len(path_parts) >= 2:
                    artist_from_path = path_parts[-2]
                    album_from_path = path_parts[-1]
                    logger.info(f"从目录路径解析: artist='{artist_from_path}', album='{album_from_path}'")
                elif len(path_parts) == 1:
                    # 只有一个目录层级，作为艺术家
                    artist_from_path = path_parts[0]
                    logger.info(f"从目录路径解析: artist='{artist_from_path}'")
            except Exception as e:
                logger.debug(f"从路径解析艺术家失败: {e}")

        # 添加艺术家关键词
        selected_artist = None
        if has_user_artist:
            selected_artist = user_input["artist"]
        elif has_meta_artist:
            selected_artist = current_meta["artist"]
        elif parsed_artist:
            selected_artist = parsed_artist
        elif artist_from_path:
            selected_artist = artist_from_path
            
        if selected_artist and selected_artist != title:
            keywords.append(selected_artist)

        # 添加专辑关键词：优先用户输入，其次元数据，然后路径提取
        selected_album = None
        if has_user_album:
            selected_album = user_input["album"]
        elif has_meta_album:
            selected_album = current_meta["album"]
        elif album_from_path:
            selected_album = album_from_path
            
        if selected_album and selected_album != title and selected_album != selected_artist:
            keywords.append(selected_album)

        return keywords

    def scrape(self, filename: str, current_meta: Dict, preferred_api: Optional[str] = None) -> Optional[Dict]:
        """
        刮削元数据
        """
        self._kugou_rate_limited = False
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
            except RateLimitException as e:
                logger.warning(f"{api_name} API 触发风控，停止刮削: {e}")
                return None
            except Exception as e:
                logger.warning(f"{api_name} API 调用失败: {e}")
                continue

        logger.warning("所有API均未找到匹配的元数据")
        return None

    def _scrape_by_api(self, api_name: str, keywords: List[str]) -> Optional[Dict]:
        """
        使用指定的 API 刮削
        """
        keyword = ",".join(keywords)
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
                    if song_info and song_info.get("errcode") == 1002:
                        raise RateLimitException(f"关键词 '{keyword}' 通过 {api_name} 触发风控")
                if song_info:
                    result_dict = self._song_info_to_dict(song_info, api_name)
                    result_dict["_id"] = search_results[0]["idOrMd5"]
                    return result_dict
        except RateLimitException:
            raise
        except Exception as e:
            logger.warning(f"关键词 '{keyword}' 通过 {api_name} 搜索失败: {e}")
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

    def search_all_apis(self, filename: str, current_meta: Dict, exclude_ids: List[str] = None, user_input: Dict = None) -> Dict[str, List[Dict]]:
        """
        批量搜索 API，优先使用 cloud，如果返回 0 条结果则使用 kugou
        每个API返回最多5条结果，按匹配度排序
        如果指定了 exclude_ids，则会排除这些歌曲ID后返回最多5条结果
        
        :param user_input: 用户输入的关键词（来自前端输入框），优先级最高
        """
        self._kugou_rate_limited = False
        keywords = self._build_search_keywords(filename, current_meta, user_input)
        logger.info(f"批量搜索开始，关键词: {keywords}")

        results = {
            "cloud": [],
            "kugou": []
        }

        # 优先使用 cloud API
        try:
            cloud_results = self._search_api_with_multiple_results("cloud", keywords, current_meta, exclude_ids)
            results["cloud"] = cloud_results
            logger.info(f"cloud API 返回 {len(cloud_results)} 条结果")
        except Exception as e:
            logger.warning(f"cloud API 批量搜索失败: {e}")
            results["cloud"] = []

        # 如果 cloud 返回 0 条结果，则使用 kugou API
        if len(results["cloud"]) == 0:
            logger.info("cloud API 返回 0 条结果，尝试使用 kugou API")
            try:
                kugou_results = self._search_api_with_multiple_results("kugou", keywords, current_meta, exclude_ids)
                results["kugou"] = kugou_results
                logger.info(f"kugou API 返回 {len(kugou_results)} 条结果")
            except Exception as e:
                logger.warning(f"kugou API 批量搜索失败: {e}")
                results["kugou"] = []
        else:
            logger.info("cloud API 有结果，不使用 kugou API")

        total = sum(len(v) for v in results.values())
        logger.info(f"批量搜索完成，总共返回 {total} 条结果")

        return results

    def _fetch_song_detail(self, api_name: str, search_result: Dict, keywords: List[str]) -> Optional[Dict]:
        """
        获取单条歌曲详情（供多线程调用）
        """
        try:
            song_info = None
            if api_name == "cloud":
                song_info = NeteaseApi.get_song_info(search_result["idOrMd5"])
            elif api_name == "kugou":
                song_info = KugouApi.get_song_info(search_result["idOrMd5"])
                if song_info.get("errcode") == 1002:
                    self._kugou_rate_limited = True
                    return None
            if song_info:
                result_dict = self._song_info_to_dict(song_info, api_name)
                result_dict["_id"] = search_result["idOrMd5"]
                self._calculate_match_score(result_dict, keywords)
                return result_dict
        except Exception as e:
            logger.warning(f"获取 {api_name} 歌曲详情失败: {e}")
        return None

    def _search_api_with_multiple_results(self, api_name: str, keywords: List[str], current_meta: Dict = None, exclude_ids: List[str] = None) -> List[Dict]:
        """
        搜索并返回最多5条结果，按匹配度排序
        先收集所有关键词的搜索结果，然后统一评分排序取前5
        获取歌曲详情使用多线程并行
        如果指定了 exclude_ids，则会排除这些歌曲ID后返回最多5条结果
        """
        all_results = []
        keyword = ",".join(keywords)
        exclude_set = set(exclude_ids or [])
        # 如果有排除列表，需要获取更多结果以便排除后仍有足够的选择
        fetch_limit = 20 if exclude_ids else 10

        try:
            if api_name == "cloud":
                search_results = NeteaseApi.search_song(keyword)
                logger.info(f"{api_name} API 返回 {len(search_results)} 条结果")
            elif api_name == "kugou":
                search_results = KugouApi.search_hash(keyword)
                logger.info(f"{api_name} API 返回 {len(search_results)} 条结果")

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(self._fetch_song_detail, api_name, sr, keywords)
                    for sr in search_results[:fetch_limit]
                ]
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        all_results.append(result)

        except Exception as e:
            logger.warning(f"关键词 '{keyword}' 通过 {api_name} 搜索失败: {e}")

        if api_name == "kugou" and self._kugou_rate_limited:
            logger.warning(f"{api_name} API 触发风控，清空该API的所有结果")
            all_results = []

        # 按匹配度排序
        all_results = sorted(all_results, key=lambda x: x.get("_match_score", 0), reverse=True)
        
        # 如果有排除列表，先排除已显示的结果（使用 idOrMd5）
        if exclude_set:
            original_count = len(all_results)
            all_results = [r for r in all_results if r.get("_id") not in exclude_set]
            logger.info(f"{api_name} API 排除 {original_count - len(all_results)} 条结果后剩余 {len(all_results)} 条")
        
        # 返回最多5条
        return_limit = 5
        all_results = all_results[:return_limit]
        for i, r in enumerate(all_results):
            r["_sort_index"] = i
        logger.debug(f"{api_name} API 搜索结果（按匹配度排序）: {[(r.get('title'), r.get('artist'), r.get('_match_score')) for r in all_results]}")

        return all_results

    def scrape_artist_avatar(self, artist: str) -> tuple[Optional[bytes], Optional[str]]:
        """
        刮削艺术家头像
        Returns: (image_data, successful_artist_name) or (None, None) if all failed
        """
        artist_names = [a.strip() for a in artist.split(",")]
        image_data = None
        successful_artist = None

        for name in artist_names:
            if not name:
                continue
            try:
                image_data = NeteaseApi.download_artist_avatar(name)
                if image_data and len(image_data) >= 1000:
                    successful_artist = name
                    break
            except Exception as exc:
                logger.warning(f"netease api failed for artist '{name}': {exc}")
                continue

        if not image_data or len(image_data) < 1000:
            separators = ["/", "&", "\\", "、",";"]
            for name in artist_names:
                if not name:
                    continue
                for sep in separators:
                    if sep in name:
                        fallback_name = name.split(sep)[0].strip()
                        if fallback_name and fallback_name != name:
                            try:
                                image_data = NeteaseApi.download_artist_avatar(fallback_name)
                                if image_data and len(image_data) >= 1000:
                                    successful_artist = fallback_name
                                    logger.info(f"artist avatar fallback succeeded: '{name}' -> '{fallback_name}'")
                                    break
                            except Exception as exc:
                                logger.warning(f"netease api fallback failed for artist '{fallback_name}': {exc}")
                            if image_data and len(image_data) >= 1000:
                                break
                if image_data and len(image_data) >= 1000:
                    break

        return image_data, successful_artist
