"""
配置文件
"""
import os
from pathlib import Path

ACCESS_KEY = os.environ.get("ACCESS_KEY", "tunetree-2026")
MUSIC_ROOT = os.environ.get("MUSIC_ROOT", "/app/music")
DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "library.db")
SECRET_KEY = "change-me-in-production-please"
