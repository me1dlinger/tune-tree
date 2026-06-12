"""
相似艺术家检测服务
使用多维度自适应相似度比较，而非固定阈值
"""

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from repository.artist_repository import get_all_artist_names

logger = logging.getLogger("tunetree")

_cc_t2s = None
_cc_s2t = None
_opencc_available = None


def _get_opencc():
    global _cc_t2s, _cc_s2t, _opencc_available
    if _opencc_available is None:
        try:
            from opencc import OpenCC

            _cc_t2s = OpenCC("t2s")
            _cc_s2t = OpenCC("s2t")
            _opencc_available = True
        except ImportError:
            _opencc_available = False
    return _opencc_available


def _to_base_form(text: str) -> str:
    """NFKC正规化 + 小写 + 去除空白和标点，用于最宽松的比较"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"[\s\u3000\-_\.·・†‡★☆♡♥●○◆◇■□▲△]+", "", text)
    text = re.sub(r"[^\w]", "", text)
    return text


def _char_set_similarity(a: str, b: str) -> float:
    """字符集合相似度：两个名称包含的字符重叠程度"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _substring_containment(a: str, b: str) -> float:
    """子串包含度：较短名称是否是较长名称的子串"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return len(shorter) / len(longer)
    return 0.0


def _edit_similarity(a: str, b: str) -> float:
    """编辑距离相似度 (SequenceMatcher)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _is_likely_variant(
    a_name: str,
    b_name: str,
    simplified_map: dict | None = None,
    traditional_map: dict | None = None,
) -> bool:
    if not _get_opencc():
        return False
    a_s = simplified_map.get(a_name) if simplified_map else _cc_t2s.convert(a_name)
    b_s = simplified_map.get(b_name) if simplified_map else _cc_t2s.convert(b_name)
    if a_s == b_s:
        return True
    a_t = traditional_map.get(a_name) if traditional_map else _cc_s2t.convert(a_name)
    b_t = traditional_map.get(b_name) if traditional_map else _cc_s2t.convert(b_name)
    if a_t == b_t:
        return True
    return False


def compute_similarity(
    name_a: str,
    name_b: str,
    name_a_norm: str = "",
    name_b_norm: str = "",
    simplified_map: dict | None = None,
    traditional_map: dict | None = None,
) -> float:
    """计算两个艺术家名称的综合相似度

    多维度评分：
    1. 基础形式完全匹配 (NFKC后) → 直接返回1.0
    2. 简繁体等价 → 0.95
    3. 子串包含关系 → 高分
    4. 字符集重叠度 + 编辑相似度 → 综合评分

    自适应阈值：短名称要求更高相似度，长名称允许更多差异
    """
    if not name_a or not name_b:
        return 0.0

    base_a = _to_base_form(name_a)
    base_b = _to_base_form(name_b)

    if base_a == base_b:
        return 1.0

    if _is_likely_variant(name_a, name_b, simplified_map, traditional_map):
        return 0.95

    norm_a = name_a_norm or unicodedata.normalize("NFKC", name_a)
    norm_b = name_b_norm or unicodedata.normalize("NFKC", name_b)

    containment = _substring_containment(base_a, base_b)
    char_sim = _char_set_similarity(base_a, base_b)
    edit_sim = _edit_similarity(base_a, base_b)

    if containment >= 0.8:
        score = 0.5 * containment + 0.3 * edit_sim + 0.2 * char_sim
    else:
        score = 0.5 * edit_sim + 0.3 * char_sim + 0.2 * containment

    return round(min(score, 0.99), 4)


def _adaptive_threshold(name_a: str, name_b: str) -> float:
    """自适应阈值：名称越短要求越高，名称越长允许更多差异

    - 1-2字符: 0.95 (几乎完全匹配)
    - 3字符: 0.85
    - 4-5字符: 0.80
    - 6+字符: 0.75
    """
    min_len = min(len(_to_base_form(name_a)), len(_to_base_form(name_b)))
    if min_len <= 2:
        return 0.95
    elif min_len == 3:
        return 0.85
    elif min_len <= 5:
        return 0.80
    else:
        return 0.75


def find_similar_artists(max_groups=10, library_id=None):
    artists = get_all_artist_names(library_id=library_id)
    groups = []
    seen = set()

    simplified_map = {}
    traditional_map = {}
    if _get_opencc():
        for a in artists:
            n = a["name"]
            simplified_map[n] = _cc_t2s.convert(n)
            traditional_map[n] = _cc_s2t.convert(n)

    for i in range(len(artists)):
        aid = artists[i]["id"]
        if aid in seen:
            continue
        for j in range(i + 1, len(artists)):
            bid = artists[j]["id"]
            if bid in seen:
                continue
            name_a = artists[i]["name"]
            name_b = artists[j]["name"]
            norm_a = artists[i].get("name_normalized") or name_a
            norm_b = artists[j].get("name_normalized") or name_b
            if not name_a or not name_b:
                continue

            sim = compute_similarity(
                name_a, name_b, norm_a, norm_b, simplified_map, traditional_map
            )
            threshold = _adaptive_threshold(name_a, name_b)

            if sim >= threshold:
                groups.append(
                    {
                        "artist_a": {
                            "id": artists[i]["id"],
                            "name": artists[i]["name"],
                        },
                        "artist_b": {
                            "id": artists[j]["id"],
                            "name": artists[j]["name"],
                        },
                        "similarity": sim,
                    }
                )
                seen.add(aid)
                seen.add(bid)
                break

    groups.sort(key=lambda g: g["similarity"], reverse=True)
    return groups[:max_groups]
