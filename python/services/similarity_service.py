"""
相似艺术家检测服务
使用多维度自适应相似度比较，而非固定阈值
"""

import logging
import math
import re
import unicodedata
from difflib import SequenceMatcher
from repository.artist_repository import get_all_artist_names
from repository import similarity_repository

logger = logging.getLogger("tunetree")

_cc_t2s = None
_cc_s2t = None
_opencc_available = None

# 自适应阈值（可在运行时通过 update_thresholds 配置；本服务**不做自动调参**）
#   short: 1-2 字符名称   len3: 3 字符   len5: 4-5 字符   long: 6+ 字符
DEFAULT_SIMILARITY_THRESHOLDS = {
    "short": 0.95,
    "len3": 0.85,
    "len5": 0.80,
    "long": 0.75,
}
# 运行期生效值：必须从默认值副本起步，避免调用方拿到默认值时改坏常量本身
SIMILARITY_THRESHOLDS = dict(DEFAULT_SIMILARITY_THRESHOLDS)


def get_thresholds() -> dict:
    """返回当前阈值的副本（调用方随便改，影响不到内部状态）。"""
    return dict(SIMILARITY_THRESHOLDS)


def update_thresholds(values: dict) -> dict:
    """更新阈值配置，返回更新后的新副本。

    只接受 SIMILARITY_THRESHOLDS 里已有的键，值必须是 0.5~1.0 之间的数字；
    任一非法就整体拒绝（先全部校验再落盘，不会出现改一半的情况）。
    """
    if not isinstance(values, dict):
        raise ValueError("阈值配置必须是 dict")

    unknown = set(values) - set(SIMILARITY_THRESHOLDS)
    if unknown:
        raise ValueError(f"未知阈值键: {sorted(unknown)}")

    validated = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"阈值 {key} 必须是数字，收到 {value!r}")
        number = float(value)
        if math.isnan(number) or not (0.5 <= number <= 1.0):
            raise ValueError(f"阈值 {key} 必须在 0.5~1.0 之间，收到 {value!r}")
        validated[key] = number

    SIMILARITY_THRESHOLDS.update(validated)
    return dict(SIMILARITY_THRESHOLDS)


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

    - 1-2字符: SIMILARITY_THRESHOLDS["short"] (默认 0.95，几乎完全匹配)
    - 3字符: SIMILARITY_THRESHOLDS["len3"] (默认 0.85)
    - 4-5字符: SIMILARITY_THRESHOLDS["len5"] (默认 0.80)
    - 6+字符: SIMILARITY_THRESHOLDS["long"] (默认 0.75)

    阈值来自模块级配置（见 update_thresholds），每次调用都现读，改配置立即生效。
    """
    min_len = min(len(_to_base_form(name_a)), len(_to_base_form(name_b)))
    if min_len <= 2:
        return SIMILARITY_THRESHOLDS["short"]
    elif min_len == 3:
        return SIMILARITY_THRESHOLDS["len3"]
    elif min_len <= 5:
        return SIMILARITY_THRESHOLDS["len5"]
    else:
        return SIMILARITY_THRESHOLDS["long"]


def find_similar_artists(max_groups=10, library_id=None):
    """找出可能重复的艺术家组合，按相似度降序返回前 max_groups 组。

    用户反馈（artist_similarity_feedback 表）参与筛选：
    - ``dismissed``（用户标记「不是同一人」）：跳过这一对，**且刻意不写 seen**。
      这是有意的设计——A 和 B 不是同一个人，不代表 A 和 C 也不是，所以忽略一组后
      两个艺术家仍然可以各自与别人配对（「忽略一组后新候选会自动补位」就是由此而来：
      被压下去的组合让位，后面的候选会顶上来填满 max_groups）。
    - ``confirmed``（用户标记「是同一人」）：无论名称像不像都直接产出该组，
      similarity 记为 1.0、feedback 记为 "confirmed"，并照常占用 seen。
    - 无反馈：走原本的多维度相似度 + 自适应阈值算法。

    返回值每组为 ``{artist_a, artist_b, similarity, feedback}``，
    feedback 为 ``None``（算法命中）或 ``"confirmed"``。

    性能：反馈表**只查一次**（get_feedback_map），循环内全部走内存 dict；
    不要改成逐对查库。
    """
    artists = get_all_artist_names(library_id=library_id)
    # 一次性把反馈读进内存：下面的双重循环是 O(n²)，逐对查库会直接拖垮接口
    feedback = similarity_repository.get_feedback_map()
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

            pair = (aid, bid) if aid < bid else (bid, aid)
            verdict = feedback.get(pair)

            if verdict == "dismissed":
                # 用户说「这两个不是同一人」：放过这一对，但不写 seen——
                # A 仍可能与 C 相似、B 也可能与 D 相似，不该被这一条反馈连坐。
                continue

            if verdict == "confirmed":
                # 用户说「是同一人」：直接产出，不再看名称像不像。
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
                        "similarity": 1.0,
                        "feedback": "confirmed",
                    }
                )
                seen.add(aid)
                seen.add(bid)
                break

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
                        "feedback": None,
                    }
                )
                seen.add(aid)
                seen.add(bid)
                break

    # 人工确认（confirmed）过的配对必须**始终可见**：
    # 上面的贪心 seen 可能把它们吃掉（例如 A 已与他人配对），那会让用户
    # 「明明标记了是同一人，列表里却看不到」，等于标记静默失效。
    emitted = set()
    for g in groups:
        aid, bid = g["artist_a"]["id"], g["artist_b"]["id"]
        emitted.add((min(aid, bid), max(aid, bid)))
    name_by_id = {a["id"]: a["name"] for a in artists}
    for (aid, bid), verdict in feedback.items():
        if verdict != "confirmed" or (aid, bid) in emitted:
            continue
        if aid not in name_by_id or bid not in name_by_id:
            continue
        groups.append(
            {
                "artist_a": {"id": aid, "name": name_by_id[aid]},
                "artist_b": {"id": bid, "name": name_by_id[bid]},
                "similarity": 1.0,
                "feedback": "confirmed",
            }
        )
        emitted.add((aid, bid))

    groups.sort(key=lambda g: g["similarity"], reverse=True)
    if len(groups) > max_groups:
        # max_groups 只用于限制「候选」数量；已确认的配对不参与截断
        confirmed = [g for g in groups if g.get("feedback") == "confirmed"]
        others = [g for g in groups if g.get("feedback") != "confirmed"]
        groups = confirmed + others[: max(0, max_groups - len(confirmed))]
    return groups
