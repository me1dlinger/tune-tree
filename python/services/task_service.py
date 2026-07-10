"""
定时任务服务
"""

import time
import logging
from datetime import datetime, timedelta
from repository.task_repository import (
    get_task_config,
    get_task_status,
    set_task_running,
    update_task_status,
    update_task_next_run,
    is_task_running,
    add_track_cooldown,
    is_track_on_cooldown,
    commit,
)
from repository.track_repository import (
    get_pending_tracks,
    update_track_metadata,
    recalc_pending,
    add_op_log,
    get_tracks_by_ids,
    commit as commit_track,
)
from services.scan_service import scan_library
from services.format_service import execute_format
from services.metadata_scraper import MetadataScraper
from utils.metadata import write_metadata, write_cover, write_lyrics
from repository.library_repository import (
    get_current_library_path,
    get_current_library_id,
)

# 定时任务调度器
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = None
scheduled_job_id = "scheduled_task"

logger = logging.getLogger("tunetree")
scraper = MetadataScraper()

# 任务锁，防止并发执行
_task_lock = {}

# Flask应用实例，用于在定时任务中获取应用上下文
_app = None


def set_app(app):
    """设置Flask应用实例"""
    global _app
    _app = app


def update_scheduler():
    """更新定时任务调度器"""
    global scheduler

    config = get_task_config()
    scrape_enabled = config["scrape_enabled"]
    organize_enabled = config["organize_enabled"]
    interval_minutes = config["interval_minutes"]

    if scheduler:
        # 移除旧的任务
        try:
            scheduler.remove_job(scheduled_job_id)
        except Exception:
            pass

    # 如果两个任务都没开启，不设置定时任务
    if not scrape_enabled and not organize_enabled:
        logger.info("定时任务已禁用（两个任务都未开启）")
        return

    # 创建或更新定时任务
    if not scheduler:
        scheduler = BackgroundScheduler()
        scheduler.start()

    trigger = IntervalTrigger(minutes=interval_minutes)

    def scheduled_task_wrapper():
        """定时任务包装器，确保在应用上下文内执行"""
        if _app:
            with _app.app_context():
                run_scheduled_task()
                # 更新下次执行时间
                next_run_at = datetime.now().timestamp() + (interval_minutes * 60)
                update_task_next_run("scheduled", next_run_at)
        else:
            logger.error("Flask应用实例未设置，无法执行定时任务")

    scheduler.add_job(
        scheduled_task_wrapper,
        trigger=trigger,
        id=scheduled_job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        f"定时任务已配置：间隔 {interval_minutes} 分钟，刮削={scrape_enabled}，整理={organize_enabled}"
    )


def acquire_task_lock(task_type: str) -> bool:
    """获取任务锁"""
    if _task_lock.get(task_type):
        return False
    _task_lock[task_type] = True
    return True


def release_task_lock(task_type: str):
    """释放任务锁"""
    _task_lock[task_type] = False


def run_scheduled_task():
    """执行定时任务"""
    config = get_task_config()
    scrape_enabled = config["scrape_enabled"]
    organize_enabled = config["organize_enabled"]

    # 如果两个任务都没开启，直接返回
    if not scrape_enabled and not organize_enabled:
        return

    # 检查是否有任务正在运行
    if is_task_running("scheduled"):
        logger.info("定时任务正在运行中，跳过本次执行")
        return

    if not acquire_task_lock("scheduled"):
        logger.info("定时任务锁已被占用，跳过本次执行")
        return

    try:
        set_task_running("scheduled", is_manual=0)
        commit()  # 提交运行中状态

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 扫描（scan_library 内部已记录日志）
        logger.info("定时任务开始执行：扫描")
        scan_library(
            get_current_library_path() or "", library_id=get_current_library_id()
        )

        # 2. 刮削（如果启用）
        if scrape_enabled:
            logger.info("定时任务开始执行：刮削")
            scrape_start = time.time()
            scrape_result = run_scrape_task()
            scrape_duration = _format_duration(time.time() - scrape_start)
            add_op_log(
                now,
                "scrape",
                f"定时刮削完成：成功 {scrape_result['success']} 失败 {scrape_result['failed']} 跳过 {scrape_result['skipped']} · 耗时 {scrape_duration}",
                library_id=get_current_library_id(),
            )

        # 3. 整理（如果启用）
        if organize_enabled:
            logger.info("定时任务开始执行：整理")
            organize_start = time.time()
            organize_result = run_organize_task()
            organize_duration = _format_duration(time.time() - organize_start)
            add_op_log(
                now,
                "organize",
                f"定时整理完成：移动 {organize_result['moved']} 跳过 {organize_result['skipped']} 失败 {organize_result['failed']} · 耗时 {organize_duration}",
                library_id=get_current_library_id(),
            )

        commit_track()
        update_task_status("scheduled", "success", is_manual=0)
        commit()  # 提交成功状态
        logger.info("定时任务执行完成")

    except Exception as e:
        logger.error(f"定时任务执行失败: {e}")
        update_task_status("scheduled", "failed", is_manual=0, error_message=str(e))
        commit()  # 提交失败状态
    finally:
        release_task_lock("scheduled")


def _format_duration(seconds: float) -> str:
    """格式化耗时"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}小时{minutes}分钟{secs}秒"
    if minutes > 0:
        return f"{minutes}分钟{secs}秒"
    return f"{secs}秒"


def run_scrape_task():
    """执行刮削任务"""
    result = {"success": 0, "failed": 0, "skipped": 0}

    # 获取待处理的歌曲（缺少标签的）
    pending_tracks = get_pending_tracks(library_id=get_current_library_id())

    from repository.track_repository import (
        batch_update_track_metadata as bulk_update,
        batch_recalc_pending as bulk_recalc,
    )

    pending_updates: list[tuple[int, dict]] = []
    pending_recalc: list[int] = []
    BATCH_FLUSH = 50

    for track in pending_tracks:
        track_id = track["id"]
        track_path = track["path"]

        # 检查是否在冷却期
        if is_track_on_cooldown(track_id):
            result["skipped"] += 1
            continue

        try:
            current_meta = {
                "title": track["title"],
                "artist": track["artist"],
                "album": track["album"],
            }

            # 刮削元数据
            scraped_data = scraper.scrape(track_path, current_meta)

            if scraped_data:
                # 写入元数据到文件
                meta_fields = {}
                for key in ["title", "artist", "album", "album_artist", "year", "track_num"]:
                    if key in scraped_data and scraped_data[key] is not None:
                        meta_fields[key] = scraped_data[key]

                if meta_fields:
                    updated = write_metadata(track_path, meta_fields)
                    if updated:
                        pending_updates.append((track_id, updated))
                        if any(k in updated for k in ("artist", "album", "title")):
                            pending_updates.append((track_id, {"organized": 0}))

                # 写入封面
                if scraped_data.get("_cover_data"):
                    import base64

                    cover_data = base64.b64decode(scraped_data["_cover_data"])
                    write_cover(track_path, cover_data, "image/jpeg")
                    pending_updates.append((track_id, {"has_cover": 1}))

                # 写入歌词
                if scraped_data.get("lyrics") is not None:
                    write_lyrics(track_path, scraped_data["lyrics"])
                    has_lyrics = 1 if scraped_data["lyrics"] else 0
                    pending_updates.append((track_id, {"has_lyrics": has_lyrics}))

                pending_updates.append((track_id, {"scrape_failed": 0}))
                pending_recalc.append(track_id)
                result["success"] += 1

            else:
                pending_updates.append((track_id, {"scrape_failed": 1}))
                add_track_cooldown(track_id, "scrape_failed")
                result["failed"] += 1

        except Exception as e:
            logger.error(f"刮削歌曲失败 {track['filename']}: {e}")
            pending_updates.append((track_id, {"scrape_failed": 1}))
            add_track_cooldown(track_id, f"scrape_error: {str(e)}")
            result["failed"] += 1

        # 批量刷新
        if len(pending_updates) >= BATCH_FLUSH:
            bulk_update(pending_updates)
            if pending_recalc:
                bulk_recalc(pending_recalc)
            commit()
            pending_updates.clear()
            pending_recalc.clear()

    # 刷新剩余
    if pending_updates:
        bulk_update(pending_updates)
    if pending_recalc:
        bulk_recalc(pending_recalc)
    commit()

    return result


def run_organize_task():
    """执行整理任务"""
    result = {"moved": 0, "skipped": 0, "failed": 0}

    # 获取有标签但未整理的歌曲
    from repository.track_repository import get_db

    db = get_db()
    library_id = get_current_library_id()
    if library_id:
        rows = db.execute(
            "SELECT artist, id FROM tracks WHERE pending=0 AND organized=0 AND artist IS NOT NULL AND artist != '' AND library_id=? ORDER BY artist",
            (library_id,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT artist, id FROM tracks WHERE pending=0 AND organized=0 AND artist IS NOT NULL AND artist != '' ORDER BY artist"
        ).fetchall()

    artists = {}
    for row in rows:
        artist = row["artist"]
        track_id = row["id"]

        # 检查是否在冷却期
        if is_track_on_cooldown(track_id):
            result["skipped"] += 1
            continue

        if artist not in artists:
            artists[artist] = []
        artists[artist].append(track_id)

    for artist, track_ids in artists.items():
        try:
            # 执行整理
            organize_result = execute_format(artist, track_ids=track_ids)
            result["moved"] += organize_result["moved"]
            result["skipped"] += organize_result["skipped"]
            result["failed"] += organize_result["errors"]

            # 检查冲突的文件，加入冷却
            from services.format_service import preview_format

            preview = preview_format(artist, track_ids=track_ids)
            for item in preview["items"]:
                if item["status"] == "conflict":
                    add_track_cooldown(item["track_id"], "organize_conflict")
                    result["skipped"] += 1

        except Exception as e:
            logger.error(f"整理艺术家失败 {artist}: {e}")
            result["failed"] += len(track_ids)

    return result


def run_manual_task(task_type: str):
    """手动执行任务"""
    if not acquire_task_lock(task_type):
        return {"error": "任务正在运行中"}

    try:
        set_task_running(task_type, is_manual=1)
        commit()  # 提交运行中状态
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 先执行扫描（scan_library 内部已记录日志）
        logger.info("手动任务开始执行：扫描")
        scan_library(
            get_current_library_path() or "", library_id=get_current_library_id()
        )

        if task_type == "scrape":
            # 执行刮削
            logger.info("手动任务开始执行：刮削")
            scrape_start = time.time()
            result = run_scrape_task()
            scrape_duration = _format_duration(time.time() - scrape_start)
            add_op_log(
                now,
                "scrape",
                f"手动刮削完成：成功 {result['success']} 失败 {result['failed']} 跳过 {result['skipped']} · 耗时 {scrape_duration}",
                library_id=get_current_library_id(),
            )
            commit_track()
            update_task_status("scrape", "success", is_manual=1)
            commit()  # 提交成功状态
            return result

        elif task_type == "organize":
            # 执行整理
            logger.info("手动任务开始执行：整理")
            organize_start = time.time()
            result = run_organize_task()
            organize_duration = _format_duration(time.time() - organize_start)
            add_op_log(
                now,
                "organize",
                f"手动整理完成：移动 {result['moved']} 跳过 {result['skipped']} 失败 {result['failed']} · 耗时 {organize_duration}",
                library_id=get_current_library_id(),
            )
            commit_track()
            update_task_status("organize", "success", is_manual=1)
            commit()  # 提交成功状态
            return result

        elif task_type == "both":
            # 先执行刮削
            logger.info("手动任务开始执行：刮削")
            scrape_start = time.time()
            scrape_result = run_scrape_task()
            scrape_duration = _format_duration(time.time() - scrape_start)
            add_op_log(
                now,
                "scrape",
                f"手动刮削完成：成功 {scrape_result['success']} 失败 {scrape_result['failed']} 跳过 {scrape_result['skipped']} · 耗时 {scrape_duration}",
                library_id=get_current_library_id(),
            )

            # 再执行整理
            logger.info("手动任务开始执行：整理")
            organize_start = time.time()
            organize_result = run_organize_task()
            organize_duration = _format_duration(time.time() - organize_start)
            add_op_log(
                now,
                "organize",
                f"手动整理完成：移动 {organize_result['moved']} 跳过 {organize_result['skipped']} 失败 {organize_result['failed']} · 耗时 {organize_duration}",
                library_id=get_current_library_id(),
            )

            commit_track()
            update_task_status("scheduled", "success", is_manual=1)
            commit()  # 提交成功状态
            return {"scrape": scrape_result, "organize": organize_result}

    except Exception as e:
        logger.error(f"手动任务执行失败 {task_type}: {e}")
        update_task_status(task_type, "failed", is_manual=1, error_message=str(e))
        commit()  # 提交失败状态
        return {"error": str(e)}
    finally:
        release_task_lock(task_type)
