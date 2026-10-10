"""Scheduler tasks using APScheduler."""
import logging
import threading
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
from app.models import Device, ScheduleConfig, TaskLog
from app.services.backup_service import backup_multiple_devices
from app.services.discovery_service import discover_multiple_devices
from app.services.info_service import collect_multiple_devices_info
from app.services.integration_sync_service import run_scheduled_sync
from app.config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# Tracks which schedule ids are currently executing so the UI can show a
# live "运行中" state. Keyed by schedule id (not task_type) so multiple
# schedules of the same task type can run/display independently.
_running_schedule_ids = set()
_running_lock = threading.Lock()


def get_running_schedule_ids():
    """Return a copy of the currently running schedule ids."""
    with _running_lock:
        return list(_running_schedule_ids)


def _run_integration_task(source: str, db):
    task_type = f"{source}_sync"
    task = TaskLog(
        task_type=task_type, status="running", started_at=datetime.utcnow(),
        total_devices=0, success_count=0, failed_count=0,
    )
    db.add(task)
    db.commit()
    try:
        run = run_scheduled_sync(source, db, max_attempts=3)
        task = db.get(TaskLog, task.id)
        task.status = "success"
        task.completed_at = datetime.utcnow()
        task.total_devices = run.discovered_vms
        task.success_count = run.created_count + run.updated_count + run.unchanged_count
        task.failed_count = 0
        task.summary = (
            f"{source} 同步完成：新增 {run.created_count}，更新 {run.updated_count}，"
            f"未变化 {run.unchanged_count}，失联 {run.stale_count}，尝试 {run.attempt} 次"
        )
        db.commit()
        return task
    except Exception as exc:
        db.rollback()
        task = db.get(TaskLog, task.id)
        task.status = "failed"
        task.completed_at = datetime.utcnow()
        task.failed_count = 1
        task.error_details = [{"source": source, "error": str(exc)[:1000]}]
        task.summary = f"{source} 自动同步失败（已重试）：{exc}"
        db.commit()
        raise


def _run_zabbix_sync(_device_ids, db):
    return _run_integration_task("zabbix", db)


def _run_vcenter_sync(_device_ids, db):
    from app.models import SystemSetting
    from app.services.integration_sources import provider
    sources = []
    for row in db.query(SystemSetting).filter(SystemSetting.key.like('integration_vcenter%')).all():
        source = row.key.removeprefix('integration_')
        try:
            if provider(source) == 'vcenter': sources.append(source)
        except ValueError:
            continue
    if not sources:
        raise RuntimeError('尚未配置 vCenter 来源')
    errors, result = [], None
    for source in sources:
        try:
            result = _run_integration_task(source, db)
        except Exception:
            errors.append(source)
    if errors:
        raise RuntimeError('部分来源同步失败，请查看各来源日志：' + ', '.join(errors))
    return result


# task_type -> service handler(device_ids, db) -> TaskLog
TASK_MAP = {
    "backup": backup_multiple_devices,
    "discovery": discover_multiple_devices,
    "info": collect_multiple_devices_info,
    "zabbix_sync": _run_zabbix_sync,
    "vcenter_sync": _run_vcenter_sync,
}

VALID_TASK_TYPES = set(TASK_MAP.keys())


def _run_task(schedule_id: int):
    """Dispatch a scheduled task by its schedule id.

    Each phase uses its own DB session so the handler's internal (possibly
    concurrent) session usage can never corrupt the ScheduleConfig row we
    update afterwards. ``last_run`` / ``last_run_status`` are written on the
    correct schedule row (supports multiple schedules of the same task type).
    """
    # Resolve task type without holding the row open across the handler run.
    db = SessionLocal()
    try:
        sched = db.query(ScheduleConfig).get(schedule_id)
        if not sched:
            logger.warning(f"Schedule {schedule_id} no longer exists; skipping.")
            return
        task_type = sched.task_type
    finally:
        db.close()

    handler = TASK_MAP.get(task_type)
    if not handler:
        logger.warning(f"Unknown task type '{task_type}' for schedule {schedule_id}; skipping.")
        return

    try:
        with _running_lock:
            _running_schedule_ids.add(schedule_id)

        # Collect active device ids on a throwaway session.
        ddb = SessionLocal()
        try:
            device_ids = [d.id for d in ddb.query(Device).filter(Device.is_active == True).all()]
        finally:
            ddb.close()

        if device_ids or task_type in {"zabbix_sync", "vcenter_sync"}:
            logger.info(f"Scheduled '{task_type}' (schedule {schedule_id}) starting")
            hdb = SessionLocal()
            try:
                handler(device_ids, hdb)
            finally:
                hdb.close()
            logger.info(f"Scheduled '{task_type}' (schedule {schedule_id}) completed")
        else:
            logger.info(f"Scheduled '{task_type}' (schedule {schedule_id}) skipped: no active devices")
    except Exception as e:
        logger.error(f"Scheduled task failed for schedule {schedule_id}: {e}", exc_info=True)
        _persist_schedule_result(schedule_id, task_type, failed=True)
        return
    finally:
        with _running_lock:
            _running_schedule_ids.discard(schedule_id)

    _persist_schedule_result(schedule_id, task_type, failed=False)


def _persist_schedule_result(schedule_id: int, task_type: str, failed: bool):
    """Write last_run / last_run_status onto the schedule row (its own session)."""
    udb = SessionLocal()
    try:
        sc = udb.query(ScheduleConfig).get(schedule_id)
        if not sc:
            return
        latest = (
            udb.query(TaskLog)
            .filter(TaskLog.task_type == task_type)
            .order_by(TaskLog.started_at.desc())
            .first()
        )
        sc.last_run_status = "failed" if failed else (latest.status if latest else "success")
        sc.last_run = datetime.utcnow()
        udb.commit()
    except Exception as e:
        logger.error(f"Failed to persist schedule result for {schedule_id}: {e}")
    finally:
        udb.close()


def init_scheduler():
    """Initialize and start the scheduler with default schedules."""
    db = SessionLocal()
    try:
        # Create default schedules if they don't exist
        defaults = [
            ("backup", settings.DEFAULT_BACKUP_SCHEDULE, "每日配置备份", True),
            ("discovery", settings.DEFAULT_DISCOVERY_SCHEDULE, "每日邻居发现", True),
            ("info", settings.DEFAULT_INFO_SCHEDULE, "每周设备数据采集", True),
            ("zabbix_sync", "15 */1 * * *", "Zabbix 每小时资产同步", False),
            ("vcenter_sync", "30 */1 * * *", "vCenter 每小时资产同步", False),
        ]

        for task_type, cron_expr, description, enabled in defaults:
            existing = db.query(ScheduleConfig).filter(ScheduleConfig.task_type == task_type).first()
            if not existing:
                sched = ScheduleConfig(
                    task_type=task_type,
                    cron_expression=cron_expr,
                    is_enabled=enabled,
                    description=description,
                )
                db.add(sched)
                logger.info(f"Created default schedule for {task_type}: {cron_expr}")
        db.commit()

        # Load all enabled schedules into APScheduler
        schedules = db.query(ScheduleConfig).filter(ScheduleConfig.is_enabled == True).all()
        for sched in schedules:
            _add_apscheduler_job(sched)

    except Exception as e:
        logger.error(f"Failed to initialize scheduler: {e}")
    finally:
        db.close()

    scheduler.start()
    _sync_next_runs()
    logger.info("Scheduler started")


def _add_apscheduler_job(sched: ScheduleConfig):
    """Add a job to APScheduler from a ScheduleConfig.

    The job id is derived from the schedule's primary key so multiple
    schedules (including multiple of the same task type) can coexist.
    """
    handler = TASK_MAP.get(sched.task_type)
    if not handler:
        logger.warning(f"Unknown task type: {sched.task_type}")
        return

    try:
        parts = sched.cron_expression.split()
        if len(parts) != 5:
            logger.error(f"Invalid cron expression for schedule {sched.id} ({sched.task_type}): {sched.cron_expression}")
            return

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        scheduler.add_job(
            _run_task,
            trigger=trigger,
            id=f"sched_{sched.id}",
            name=f"{sched.task_type} ({sched.description})",
            args=[sched.id],
            replace_existing=True,
        )
        logger.info(f"Scheduled job {sched.id} ({sched.task_type}): {sched.cron_expression}")
    except Exception as e:
        logger.error(f"Failed to schedule job {sched.id} ({sched.task_type}): {e}")


def _sync_next_runs():
    """Update each ScheduleConfig.next_run from the running APScheduler jobs."""
    from app.services.windows_dhcp import scheduled_sync
    scheduler.add_job(scheduled_sync, 'interval', minutes=1, id='windows_dhcp_sync', replace_existing=True, max_instances=1, coalesce=True)
    db = SessionLocal()
    try:
        for sched in db.query(ScheduleConfig).all():
            job = scheduler.get_job(f"sched_{sched.id}")
            if job is not None and job.next_run_time is not None:
                # Normalize the instant before removing timezone for legacy UTC columns.
                sched.next_run = job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                sched.next_run = None
        db.commit()
    finally:
        db.close()


def reload_scheduler():
    """Reload all schedules from database."""
    # Remove all existing jobs
    for job in scheduler.get_jobs():
        scheduler.remove_job(job.id)

    db = SessionLocal()
    try:
        schedules = db.query(ScheduleConfig).filter(ScheduleConfig.is_enabled == True).all()
        for sched in schedules:
            _add_apscheduler_job(sched)
    finally:
        db.close()

    _sync_next_runs()
    logger.info("Scheduler reloaded")


def shutdown_scheduler():
    """Shutdown the scheduler."""
    scheduler.shutdown(wait=False)
    logger.info("Scheduler shutdown")
