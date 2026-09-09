"""Dashboard API routes."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Device, ConfigBackup, Neighbor, TaskLog, DeviceGroup, DeviceInfo, ScheduleConfig
from app.scheduler.tasks import get_running_schedule_ids

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    """Get dashboard statistics."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_devices = db.query(Device).filter(Device.is_active == True).count()
    online_devices = db.query(Device).filter(Device.is_active == True, Device.status == "online").count()
    offline_devices = db.query(Device).filter(Device.is_active == True, Device.status == "offline").count()
    unknown_devices = db.query(Device).filter(Device.is_active == True, Device.status == "unknown").count()

    total_backups = db.query(ConfigBackup).count()
    backups_today = db.query(ConfigBackup).filter(ConfigBackup.backup_time >= today_start).count()
    last_backup = db.query(ConfigBackup).order_by(ConfigBackup.backup_time.desc()).first()

    total_neighbors = db.query(Neighbor).count()
    last_discovery = db.query(Neighbor).order_by(Neighbor.discovered_at.desc()).first()

    # Devices that have never been backed up
    devices_never_backed_up = db.query(Device).filter(
        Device.is_active == True, Device.last_backup == None
    ).count()

    # Unique devices with configuration changes during the rolling last 24 hours.
    devices_with_changes = db.query(
        func.count(func.distinct(ConfigBackup.device_id))
    ).filter(
        ConfigBackup.is_changed == True,
        ConfigBackup.backup_time >= now - timedelta(days=1)
    ).scalar() or 0

    # Group distribution
    groups = db.query(DeviceGroup).all()
    group_stats = []
    for g in groups:
        count = db.query(Device).filter(Device.group_id == g.id, Device.is_active == True).count()
        group_stats.append({
            "id": g.id,
            "name": g.name,
            "device_count": count,
        })

    # Recent tasks
    recent_tasks = db.query(TaskLog).order_by(TaskLog.started_at.desc()).limit(10).all()
    task_list = []
    for t in recent_tasks:
        task_list.append({
            "id": t.id,
            "task_type": t.task_type,
            "status": t.status,
            "started_at": t.started_at,
            "completed_at": t.completed_at,
            "summary": t.summary,
            "success_count": t.success_count,
            "failed_count": t.failed_count,
        })

    # Device type distribution
    type_dist = {}
    devices = db.query(Device).filter(Device.is_active == True).all()
    for d in devices:
        type_dist[d.device_type] = type_dist.get(d.device_type, 0) + 1

    # Scheduled task status
    running_ids = set(get_running_schedule_ids())
    schedules = db.query(ScheduleConfig).order_by(ScheduleConfig.task_type, ScheduleConfig.id).all()
    schedule_status = []
    for sc in schedules:
        schedule_status.append({
            "id": sc.id,
            "task_type": sc.task_type,
            "description": sc.description,
            "cron_expression": sc.cron_expression,
            "is_enabled": sc.is_enabled,
            "last_run": sc.last_run,
            "next_run": sc.next_run,
            "is_running": sc.id in running_ids,
            "last_status": sc.last_run_status,
        })

    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "unknown_devices": unknown_devices,
        "total_backups": total_backups,
        "backups_today": backups_today,
        "last_backup_time": last_backup.backup_time if last_backup else None,
        "total_neighbors": total_neighbors,
        "last_discovery_time": last_discovery.discovered_at if last_discovery else None,
        "devices_never_backed_up": devices_never_backed_up,
        "devices_with_changes": devices_with_changes,
        "groups": group_stats,
        "device_type_distribution": type_dist,
        "recent_tasks": task_list,
        "schedules": schedule_status,
    }


@router.get("/device-summary")
def get_device_summary(db: Session = Depends(get_db)):
    """Get a summary of all devices with their latest status."""
    devices = db.query(Device).filter(Device.is_active == True).order_by(Device.name).all()

    result = []
    for d in devices:
        latest_info = (
            db.query(DeviceInfo)
            .filter(DeviceInfo.device_id == d.id)
            .order_by(DeviceInfo.collected_at.desc())
            .first()
        )

        result.append({
            "id": d.id,
            "name": d.name,
            "ip_address": d.ip_address,
            "device_type": d.device_type,
            "status": d.status,
            "group": d.group.name if d.group else "",
            "model": latest_info.model if latest_info else "",
            "serial_number": latest_info.serial_number if latest_info else "",
            "os_version": latest_info.os_version if latest_info else "",
            "production_date": latest_info.production_date if latest_info else "",
            "production_date_source": latest_info.production_date_source if latest_info else "",
            "last_backup": d.last_backup,
            "last_discovery": d.last_discovery,
            "last_info_collection": d.last_info_collection,
        })

    return result
