"""WSGI entry point — gunicorn tune_tree.wsgi:application"""
from app import app, init_db
from repository.track_repository import reset_stale_scan_status

init_db()

# 服务启动时复位可能残留的「扫描中」状态：
# 若上次进程在扫描途中退出，数据库会留下 running 状态，不清理会导致
# 重启后界面永远显示“扫描中”。全新进程必然没有真正在跑的扫描线程，
# 因此这里复位是安全的，也是让「重启服务即可恢复」生效的关键。
with app.app_context():
    reset_stale_scan_status()

application = app

if __name__ == "__main__":
    init_db()
    with app.app_context():
        reset_stale_scan_status()
    app.run(debug=False, host="0.0.0.0", port=5000)