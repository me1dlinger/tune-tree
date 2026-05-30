"""
Tune Tree — Music Library Manager
Flask + Python 3.13 + SQLite + mutagen
主应用入口
"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask
from config import SECRET_KEY
from models.db import init_db, close_db
from api.routes import api_bp

def log_filename_wrapper(base_filename, when, interval):
    from datetime import datetime, timezone
    if when == "midnight":
        current_time = datetime.now(timezone.utc)
        current_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        current_time = current_time.replace(hour=23, minute=59, second=59, microsecond=999999)
        date_str = current_time.strftime("%Y-%m-%d")
        return f"{base_filename}.{date_str}.log"
    return f"{base_filename}.{when}"

os.makedirs("instance", exist_ok=True)
log_handler = TimedRotatingFileHandler(
    filename="instance/tunetree.log",
    when="midnight",
    interval=1,
    backupCount=30,
    utc=True,
    encoding="utf-8"
)
log_handler.namer = log_filename_wrapper
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

# Register blueprints
app.register_blueprint(api_bp)

# Teardown app context
app.teardown_appcontext(close_db)

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
