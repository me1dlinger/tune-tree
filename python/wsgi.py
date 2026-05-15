"""WSGI entry point — gunicorn tune_tree.wsgi:application"""
from app import app, init_db

init_db()
application = app

if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0", port=5000)