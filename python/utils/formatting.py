"""
格式化工具
"""

import re


def safe_dirname(name: str) -> str:
    if not name:
        return "Unknown"
    # 1. 替换 Windows 不允许的字符（包括 .）
    result = re.sub(r'[\\/:*?"<>|.]', "_", name)
    # 2. 移除控制字符
    result = re.sub(r"[\x00-\x1f\x7f]", "", result)
    # 3. 合并多余空格
    result = re.sub(r"\s+", " ", result)
    # 4. 去除首尾空格
    result = result.strip()
    # 5. 如果处理后为空或只有特殊字符，返回 Unknown
    return result or "Unknown"
