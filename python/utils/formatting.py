"""
格式化工具
支持 Windows 和 Linux 文件名限制
"""

import re


def get_relative_path(full_path: str, music_root: str) -> str:
    """
    获取相对于 MUSIC_ROOT 的路径
    
    Args:
        full_path: 完整路径
        music_root: 音乐根目录
    
    Returns:
        相对路径，如果路径不在 MUSIC_ROOT 下则返回原路径
    """
    if not full_path:
        return ""
    if full_path.lower().startswith(music_root.lower()):
        rel_path = full_path[len(music_root):]
        return rel_path.lstrip("/\\")
    return full_path


def safe_dirname(name: str) -> str:
    if not name:
        return "Unknown"
    
    # 1. 替换跨平台非法字符（Windows: \/:*?"<>|，Linux: /）
    result = re.sub(r'[\\/:*?"<>|]', "_", name)
    
    # 2. 移除控制字符（ASCII 0-31 和 127）
    result = re.sub(r"[\x00-\x1f\x7f]", "", result)
    
    # 3. 去除首尾空格
    result = result.strip()
    
    # 4. 处理 Linux 特殊开头字符
    # 以 - 开头容易被误认为命令行选项，替换为下划线
    if result.startswith("-"):
        result = "_" + result[1:]
    
    # 5. 将结尾的点号替换为下划线（Windows 不允许目录名以 . 结尾）
    result = re.sub(r"\.+$", "_", result)
    
    # 6. 检查 Windows 保留名称（不区分大小写）
    windows_reserved = {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"
    }
    if result.lower() in windows_reserved:
        result = result + "_"
    
    # 7. 如果处理后为空或只有特殊字符，返回 Unknown
    return result or "Unknown"
