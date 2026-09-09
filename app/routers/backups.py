"""Configuration backup API routes."""
import logging
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import UploadFile, File, Form
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from app.database import get_db
from app.models import Device, ConfigBackup, TaskLog
from app.schemas import ConfigBackupResponse, ConfigBackupDetail
from app.services.backup_service import backup_multiple_devices, get_backup_diff
from app.services import data_backup as data_backup_svc
from app.auth import verify_password
from app.api_models import BackupReviewRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.post("/run")
def run_backup(
    device_ids: List[int] = Query(...),
    db: Session = Depends(get_db),
):
    """Trigger configuration backup for specified devices."""
    # Verify devices exist
    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    if not devices:
        raise HTTPException(status_code=404, detail="No devices found")

    task = backup_multiple_devices(device_ids, db)
    return {
        "task_id": task.id,
        "status": task.status,
        "total_devices": task.total_devices,
        "success_count": task.success_count,
        "failed_count": task.failed_count,
        "summary": task.summary,
        "error_details": task.error_details,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


@router.post("/run-all")
def run_backup_all(db: Session = Depends(get_db)):
    """Trigger backup for all active devices."""
    device_ids = [d.id for d in db.query(Device).filter(Device.is_active == True).all()]
    if not device_ids:
        raise HTTPException(status_code=404, detail="No active devices found")

    task = backup_multiple_devices(device_ids, db)
    return {
        "task_id": task.id,
        "status": task.status,
        "total_devices": task.total_devices,
        "success_count": task.success_count,
        "failed_count": task.failed_count,
        "summary": task.summary,
        "error_details": task.error_details,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


@router.get("", response_model=List[ConfigBackupResponse])
def list_backups(
    device_id: Optional[int] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List configuration backups."""
    query = db.query(ConfigBackup)
    if device_id:
        query = query.filter(ConfigBackup.device_id == device_id)
    return query.order_by(ConfigBackup.backup_time.desc()).limit(limit).all()


@router.get("/changes")
def list_configuration_changes(
    device_id: Optional[int] = None,
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Configuration drift events plus their review and baseline context."""
    allowed_statuses = {"pending", "expected", "unexpected", "ignored"}
    if status and status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="无效的变更状态")

    # Lightweight history is enough to derive previous/latest/baseline links;
    # deliberately avoid loading the potentially large config_text column.
    history_query = db.query(
        ConfigBackup.id,
        ConfigBackup.device_id,
        ConfigBackup.config_hash,
        ConfigBackup.backup_time,
        ConfigBackup.is_baseline,
    )
    if device_id:
        history_query = history_query.filter(ConfigBackup.device_id == device_id)
    history = history_query.order_by(
        ConfigBackup.device_id, ConfigBackup.backup_time, ConfigBackup.id
    ).all()

    previous_by_id = {}
    latest_by_device = {}
    baseline_by_device = {}
    for row in history:
        previous = latest_by_device.get(row.device_id)
        previous_by_id[row.id] = previous.id if previous else None
        latest_by_device[row.device_id] = row
        if row.is_baseline:
            baseline_by_device[row.device_id] = row

    changes_query = (
        db.query(
            ConfigBackup.id,
            ConfigBackup.device_id,
            ConfigBackup.backup_time,
            ConfigBackup.config_hash,
            ConfigBackup.change_summary,
            ConfigBackup.backup_size,
            ConfigBackup.review_status,
            ConfigBackup.review_note,
            ConfigBackup.reviewed_by,
            ConfigBackup.reviewed_at,
            ConfigBackup.is_baseline,
            Device.name.label("device_name"),
            Device.ip_address.label("device_ip"),
        )
        .join(Device, Device.id == ConfigBackup.device_id)
        .filter(ConfigBackup.is_changed.is_(True))
    )
    if device_id:
        changes_query = changes_query.filter(ConfigBackup.device_id == device_id)
    if status:
        changes_query = changes_query.filter(ConfigBackup.review_status == status)
    changes = changes_query.order_by(ConfigBackup.backup_time.desc()).limit(limit).all()

    count_query = db.query(ConfigBackup.review_status, func.count(ConfigBackup.id)).filter(
        ConfigBackup.is_changed.is_(True)
    )
    if device_id:
        count_query = count_query.filter(ConfigBackup.device_id == device_id)
    counts = {key: value for key, value in count_query.group_by(ConfigBackup.review_status).all()}

    drifted_devices = 0
    for did, latest in latest_by_device.items():
        baseline = baseline_by_device.get(did)
        if baseline and latest.config_hash != baseline.config_hash:
            drifted_devices += 1

    items = []
    for change in changes:
        baseline = baseline_by_device.get(change.device_id)
        latest = latest_by_device.get(change.device_id)
        items.append(
            {
                "id": change.id,
                "device_id": change.device_id,
                "device_name": change.device_name,
                "device_ip": change.device_ip,
                "backup_time": change.backup_time,
                "config_hash": change.config_hash,
                "change_summary": (change.change_summary or "")[:1000],
                "backup_size": change.backup_size or 0,
                "review_status": change.review_status or "pending",
                "review_note": change.review_note or "",
                "reviewed_by": change.reviewed_by or "",
                "reviewed_at": change.reviewed_at,
                "is_baseline": bool(change.is_baseline),
                "previous_backup_id": previous_by_id.get(change.id),
                "baseline_id": baseline.id if baseline else None,
                "is_latest": bool(latest and latest.id == change.id),
                "device_drifted": bool(
                    baseline and latest and baseline.config_hash != latest.config_hash
                ),
            }
        )
    device_rows = (
        db.query(Device.id, Device.name, Device.ip_address)
        .join(ConfigBackup, ConfigBackup.device_id == Device.id)
        .distinct()
        .order_by(Device.name)
        .all()
    )
    return {
        "summary": {
            "total": sum(counts.values()),
            "pending": counts.get("pending", 0),
            "expected": counts.get("expected", 0),
            "unexpected": counts.get("unexpected", 0),
            "ignored": counts.get("ignored", 0),
            "drifted_devices": drifted_devices,
        },
        "devices": [
            {"id": row.id, "name": row.name, "ip_address": row.ip_address}
            for row in device_rows
        ],
        "items": items,
    }


@router.post("/{backup_id}/review")
def review_configuration_change(
    backup_id: int,
    payload: BackupReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    backup = db.get(ConfigBackup, backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if not backup.is_changed and payload.status not in {"pending", "ignored"}:
        raise HTTPException(status_code=400, detail="无变化的备份不能标记为预期或异常变更")
    user = getattr(request.state, "user", None)
    backup.review_status = payload.status
    backup.review_note = payload.note.strip()
    if payload.status == "pending":
        backup.reviewed_by = ""
        backup.reviewed_at = None
    else:
        backup.reviewed_by = user.username if user else ""
        backup.reviewed_at = datetime.utcnow()
    db.commit()

    request.state.audit_action = "config_change.review"
    request.state.audit_resource_type = "config_backup"
    request.state.audit_resource_id = backup.id
    request.state.audit_detail = {
        "device_id": backup.device_id,
        "review_status": backup.review_status,
        "has_note": bool(backup.review_note),
    }
    return {"ok": True, "id": backup.id, "review_status": backup.review_status}


@router.post("/{backup_id}/baseline")
def mark_configuration_baseline(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    backup = db.get(ConfigBackup, backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    db.query(ConfigBackup).filter(
        ConfigBackup.device_id == backup.device_id,
        ConfigBackup.is_baseline.is_(True),
    ).update({ConfigBackup.is_baseline: False}, synchronize_session=False)
    backup.is_baseline = True
    db.commit()

    request.state.audit_action = "config_change.set_baseline"
    request.state.audit_resource_type = "config_backup"
    request.state.audit_resource_id = backup.id
    request.state.audit_detail = {"device_id": backup.device_id}
    return {"ok": True, "id": backup.id, "device_id": backup.device_id}


@router.get("/{backup_id}", response_model=ConfigBackupDetail)
def get_backup(backup_id: int, db: Session = Depends(get_db)):
    """Get a specific backup with full config text."""
    backup = db.query(ConfigBackup).get(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    device = db.query(Device).get(backup.device_id)
    return ConfigBackupDetail(
        id=backup.id,
        device_id=backup.device_id,
        device_name=device.name if device else "Unknown",
        config_text=backup.config_text,
        config_hash=backup.config_hash,
        backup_time=backup.backup_time,
        is_changed=backup.is_changed,
        change_summary=backup.change_summary,
        backup_size=backup.backup_size,
        review_status=backup.review_status,
        review_note=backup.review_note or "",
        reviewed_by=backup.reviewed_by or "",
        reviewed_at=backup.reviewed_at,
        is_baseline=backup.is_baseline,
    )


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db)):
    """Download backup config as a file."""
    from fastapi.responses import PlainTextResponse

    backup = db.query(ConfigBackup).get(backup_id)
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    device = db.query(Device).get(backup.device_id)
    filename = f"{device.name}_{backup.backup_time.strftime('%Y%m%d_%H%M%S')}.cfg" if device else "backup.cfg"

    return PlainTextResponse(
        content=backup.config_text,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/diff/{backup_id_1}/{backup_id_2}")
def get_diff(backup_id_1: int, backup_id_2: int, db: Session = Depends(get_db)):
    """Get diff between two backups."""
    diff = get_backup_diff(backup_id_1, backup_id_2, db)
    return {"diff": diff}


@router.get("/device/{device_id}/history")
def get_backup_history(
    device_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get backup history for a specific device."""
    backups = (
        db.query(ConfigBackup)
        .filter(ConfigBackup.device_id == device_id)
        .order_by(ConfigBackup.backup_time.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": b.id,
            "backup_time": b.backup_time,
            "is_changed": b.is_changed,
            "change_summary": b.change_summary[:200] if b.change_summary else "",
            "backup_size": b.backup_size,
            "config_hash": b.config_hash[:16] + "...",
            "review_status": b.review_status,
            "review_note": b.review_note or "",
            "reviewed_by": b.reviewed_by or "",
            "reviewed_at": b.reviewed_at,
            "is_baseline": b.is_baseline,
        }
        for b in backups
    ]


@router.get("/tasks")
def list_backup_tasks(limit: int = 20, db: Session = Depends(get_db)):
    """List recent backup task logs."""
    tasks = (
        db.query(TaskLog)
        .filter(TaskLog.task_type == "backup")
        .order_by(TaskLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": t.id,
            "task_type": t.task_type,
            "status": t.status,
            "started_at": t.started_at,
            "completed_at": t.completed_at,
            "total_devices": t.total_devices,
            "success_count": t.success_count,
            "failed_count": t.failed_count,
            "summary": t.summary,
            "error_details": t.error_details,
        }
        for t in tasks
    ]


# ===================== App-data backup (upgrade/iteration safety) =====================
app_data_router = APIRouter(prefix="/api/backups/data", tags=["backups", "data-backup"])


def _require_superuser(request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.is_superuser:
        raise HTTPException(status_code=403, detail="仅超级管理员可管理应用数据备份")
    return user


@app_data_router.post("")
def create_data_backup(request: Request):
    """One-click snapshot of the application's own data (DB + config files).

    Run this BEFORE an upgrade/iteration so a bad change can be rolled back.
    """
    _require_superuser(request)
    result = data_backup_svc.create_data_backup()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "backup failed"))
    return result


@app_data_router.get("")
def list_data_backups(request: Request, limit: int = Query(50, ge=1, le=100)):
    """List existing app-data snapshots, newest first."""
    _require_superuser(request)
    return data_backup_svc.list_data_backups(limit=limit)


@app_data_router.get("/{name}/download")
def download_data_backup(name: str, request: Request):
    """Download a snapshot folder as a zip archive."""
    _require_superuser(request)
    try:
        data = data_backup_svc.get_data_backup_zip(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="backup not found")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=netmgr-data-{name}.zip"},
    )


@app_data_router.post("/restore")
async def restore_data_backup_api(
    request: Request,
    file: UploadFile = File(...),
    current_password: str = Form(...),
):
    """Upload a previously exported backup zip and restore the app data.

    The service takes a pre-restore safety snapshot first, then disposes the
    live engine and swaps the DB + config files from the uploaded archive.
    """
    user = _require_superuser(request)
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="当前管理员密码不正确")

    data = await file.read(data_backup_svc.MAX_ARCHIVE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > data_backup_svc.MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="备份包超过 256 MB 限制")
    result = await run_in_threadpool(data_backup_svc.restore_data_backup, data)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "恢复失败"))
    return result

