"""
Tune Tree — Music Library Manager
Flask + Python 3.13 + SQLite + mutagen
主应用入口
"""
import os
import logging
from flask import Flask
from config import SECRET_KEY
from models.db import init_db, close_db
from api.routes import api_bp

# Logging
os.makedirs("instance", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("instance/tunetree.log"),
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
