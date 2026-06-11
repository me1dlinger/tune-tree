"""
定时任务相关的数据访问层
"""

import time
from models.db import get_db

# === Track Cooldown 操作 ===


def add_track_cooldown(track_id: int, reason: str, cooldown_days: int = 3):
    """添加歌曲冷却记录"""
    db = get_db()
    cooldown_until = time.time() + (cooldown_days * 24 * 60 * 60)
    db.execute(
        """
        INSERT INTO track_cooldown (track_id, cooldown_until, reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (track_id, cooldown_until, reason, time.time()),
    )
    db.commit()


def get_track_cooldown(track_id: int):
    """获取歌曲的冷却记录"""
    db = get_db()
    return db.execute(
        "SELECT * FROM track_cooldown WHERE track_id=? ORDER BY cooldown_until DESC LIMIT 1",
        (track_id,),
    ).fetchone()


def is_track_on_cooldown(track_id: int):
    """检查歌曲是否在冷却期"""
    db = get_db()
    row = db.execute(
        "SELECT cooldown_until FROM track_cooldown WHERE track_id=? AND cooldown_until > ? ORDER BY cooldown_until DESC LIMIT 1",
        (track_id, time.time()),
    ).fetchone()
    return row is not None


def get_cooldown_tracks():
    """获取所有冷却中的歌曲"""
    db = get_db()
    return db.execute(
        "SELECT * FROM track_cooldown WHERE cooldown_until > ? ORDER BY cooldown_until DESC",
        (time.time(),),
    ).fetchall()


def delete_track_cooldown(track_id: int):
    """删除歌曲的冷却记录"""
    db = get_db()
    db.execute("DELETE FROM track_cooldown WHERE track_id=?", (track_id,))
    db.commit()


# === Task Config 操作 ===


def get_task_config():
    """获取任务配置"""
    db = get_db()
    row = db.execute("SELECT * FROM task_config ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        return dict(row)
    # 返回默认配置
    return {
        "id": None,
        "scrape_enabled": 0,
        "organize_enabled": 0,
        "interval_minutes": 60,
    }


def set_task_config(scrape_enabled: int, organize_enabled: int, interval_minutes: int):
    """设置任务配置"""
    db = get_db()
    now = time.time()
    current = get_task_config()
    if current["id"]:
        db.execute(
            """
            UPDATE task_config 
            SET scrape_enabled=?, organize_enabled=?, interval_minutes=?, updated_at=?
            WHERE id=?
            """,
            (scrape_enabled, organize_enabled, interval_minutes, now, current["id"]),
        )
    else:
        db.execute(
            """
            INSERT INTO task_config (scrape_enabled, organize_enabled, interval_minutes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scrape_enabled, organize_enabled, interval_minutes, now, now),
        )
    db.commit()


# === Task Status 操作 ===


def get_task_status(task_type: str):
    """获取任务状态"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM task_status WHERE task_type=?", (task_type,)
    ).fetchone()
    if row:
        return dict(row)
    # 返回默认状态
    return {
        "id": None,
        "task_type": task_type,
        "status": "idle",
        "last_run_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "next_run_at": None,
        "error_message": None,
        "run_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "is_manual": 0,
    }


def update_task_status(
    task_type: str, status: str, is_manual: int = 0, error_message: str = None
):
    """更新任务状态"""
    db = get_db()
    now = time.time()
    current = get_task_status(task_type)

    run_count = current["run_count"] + 1
    success_count = current["success_count"]
    failure_count = current["failure_count"]
    last_success_at = current["last_success_at"]
    last_failure_at = current["last_failure_at"]

    if status == "success":
        success_count += 1
        last_success_at = now
    elif status == "failed":
        failure_count += 1
        last_failure_at = now

    if current["id"]:
        db.execute(
            """
            UPDATE task_status 
            SET status=?, last_run_at=?, last_success_at=?, last_failure_at=?, 
                error_message=?, run_count=?, success_count=?, failure_count=?,
                is_manual=?, updated_at=?
            WHERE task_type=?
            """,
            (
                status,
                now,
                last_success_at,
                last_failure_at,
                error_message,
                run_count,
                success_count,
                failure_count,
                is_manual,
                now,
                task_type,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO task_status 
            (task_type, status, last_run_at, last_success_at, last_failure_at,
             error_message, run_count, success_count, failure_count, is_manual, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_type,
                status,
                now,
                last_success_at,
                last_failure_at,
                error_message,
                run_count,
                success_count,
                failure_count,
                is_manual,
                now,
            ),
        )
    db.commit()


def set_task_running(task_type: str, is_manual: int = 0):
    """设置任务为运行中"""
    db = get_db()
    now = time.time()
    current = get_task_status(task_type)

    run_count = current["run_count"] + 1

    if current["id"]:
        db.execute(
            """
            UPDATE task_status 
            SET status='running', last_run_at=?, run_count=?, is_manual=?, updated_at=?
            WHERE task_type=?
            """,
            (now, run_count, is_manual, now, task_type),
        )
    else:
        db.execute(
            """
            INSERT INTO task_status 
            (task_type, status, last_run_at, run_count, is_manual, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_type, "running", now, run_count, is_manual, now),
        )
    db.commit()


def update_task_next_run(task_type: str, next_run_at: float):
    """更新任务下次运行时间"""
    db = get_db()
    db.execute(
        "UPDATE task_status SET next_run_at=?, updated_at=? WHERE task_type=?",
        (next_run_at, time.time(), task_type),
    )
    db.commit()


def is_task_running(task_type: str):
    """检查任务是否正在运行"""
    status = get_task_status(task_type)
    return status["status"] == "running"


def commit():
    """提交事务"""
    db = get_db()
    db.commit()
