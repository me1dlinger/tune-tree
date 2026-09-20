"""
配置文件
"""
import os
import secrets
from pathlib import Path

ACCESS_KEY = os.environ.get("ACCESS_KEY", "tunetree-2026")
DB_ROOT = os.environ.get("DB_ROOT",  os.path.join(os.path.dirname(__file__), "instance"))
DB_PATH = os.path.join(DB_ROOT, "library.db")
# 会话密钥：优先读环境变量，其次生成随机值（生产环境请务必通过 SECRET_KEY 指定）
SECRET_KEY = os.environ.get("SECRET_KEY", "") or secrets.token_hex(32)
