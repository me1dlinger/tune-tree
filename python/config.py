"""
配置文件

配置优先级（高 → 低）：
  1. 进程环境变量（docker-compose 的 environment、shell export 等）
  2. .env 文件（应用目录 python/.env，容器内为 /app/tune-tree/.env）
  3. 代码内置默认值

也就是说：docker-compose 里 environment 配置的变量，始终优先于 .env 文件。
"""

import os
import secrets
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent


def _parse_env_file(path: Path) -> dict:
    """解析极简 .env 文件并返回键值对。

    支持：空行、`#` 注释、`export KEY=VALUE` 前缀、单/双引号包裹的值、
    未加引号值尾部的行内注释（` #`）。文件不存在或读取失败时返回空字典。
    """
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            comment_at = value.find(" #")
            if comment_at != -1:
                value = value[:comment_at].rstrip()
        values[key] = value
    return values


def _load_dotenv() -> None:
    """把应用目录下的 .env 加载进 os.environ。

    - 只读取应用目录：python/.env（容器内为 /app/tune-tree/.env）
    - 已存在于 os.environ 的变量一律不覆盖，
      因此 docker-compose 的 environment / 宿主环境变量优先级最高
    """
    for key, value in _parse_env_file(_APP_DIR / ".env").items():
        os.environ.setdefault(key, value)


_load_dotenv()

ACCESS_KEY = os.environ.get("ACCESS_KEY", "tunetree-2026")
DB_ROOT = os.environ.get("DB_ROOT", os.path.join(os.path.dirname(__file__), "instance"))
DB_PATH = os.path.join(DB_ROOT, "library.db")
# 会话密钥：优先读环境变量，其次生成随机值（生产环境请务必通过 SECRET_KEY 指定）
SECRET_KEY = os.environ.get("SECRET_KEY", "") or secrets.token_hex(32)
