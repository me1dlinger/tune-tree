"""
Tune Tree — Music Library Manager
Flask + Python 3.13 + SQLite + mutagen
主应用入口
"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request
from config import SECRET_KEY
from models.db import init_db, close_db
from api.routes import api_bp
from services.task_service import set_app, update_scheduler

def make_log_filename_wrapper(when, interval):
    def log_filename_wrapper(base_filename):
        from datetime import datetime, timezone
        if when == "midnight":
            current_time = datetime.now(timezone.utc)
            current_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            current_time = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)
            date_str = current_time.strftime("%Y-%m-%d")
            return f"{base_filename}.{date_str}.log"
        return f"{base_filename}.{when}"
    return log_filename_wrapper

os.makedirs("instance", exist_ok=True)
log_handler = TimedRotatingFileHandler(
    filename="instance/tunetree.log",
    when="midnight",
    interval=1,
    backupCount=30,
    utc=True,
    encoding="utf-8"
)
log_handler.namer = make_log_filename_wrapper("midnight", 1)
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        log_handler,
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("tunetree")

# App initialization
app = Flask(__name__)
app.secret_key = SECRET_KEY

# 限制请求体上限，防止超大上传把磁盘/内存打满（上传接口另有单文件上限）
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

# 注册蓝图
app.register_blueprint(api_bp)


# 静态资源缓存策略：
#  - /static/* 已由前端 URL 通过 ?v= 做版本控制，允许长期缓存
#  - 首页（index.html）与 /api/* 一律不缓存，保证 HTML/接口始终最新
@app.after_request
def _apply_cache_headers(resp):
    path = request.path
    if path.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# Teardown app context
app.teardown_appcontext(close_db)

if __name__ == "__main__":
    init_db()
    
    # 设置Flask应用实例供定时任务使用
    set_app(app)
    
    # 初始化定时任务调度器（需要应用上下文）
    with app.app_context():
        update_scheduler()
    
    # 默认关闭 debug（Werkzeug 调试器允许任意代码执行，切勿在生产开放）
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "").lower() == "1",
        host="0.0.0.0",
        port=5000,
    )
