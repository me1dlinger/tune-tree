"""
格式化工具
"""
import re

def safe_dirname(name: str) -> str:
    if not name:
        return "Unknown"
    result = re.sub(r'[\\/:*?"<>|]', "_", name)
    result = re.sub(r'[\x00-\x1f\x7f]', '', result)
    result = re.sub(r'\s+', ' ', result)
    return result.strip() or "Unknown"
