"""System logs API + UI route.

Powers the /system/logs page and exposes DataLog to the operator.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings, RESOURCE_DIR
from app.api_models import LogSettingsRequest
from app.database import get_db
from app.models import AuditLog, DataLog, SystemSetting
from app.services.log_service import get_log_stats, prune_operational_history


logger = logging.getLogger(__name__)

router = APIRouter(tags=["logs"])
templates = Jinja2Templates(directory=str(RESOURCE_DIR / "templates"))
templates.env.globals["APP_VERSION"] = settings.APP_VERSION
templates.env.globals["app_name"] = settings.APP_NAME


# ---- Settings helpers ----

_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_LOG_LEVEL = "INFO"
_VALID_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}


def _get_settings(db: Session) -> dict:
    """Read log-related settings from SystemSetting (json-encoded values)."""
    import json
    out = {
        "retention_days": _DEFAULT_RETENTION_DAYS,
        "audit_retention_days": 365,
        "task_retention_days": 180,
        "sync_retention_days": 180,
        "log_level": _DEFAULT_LOG_LEVEL,
    }
    row = db.query(SystemSetting).filter(SystemSetting.key == "system_log").first()
    if row and row.value:
        try:
            stored = json.loads(row.value)
            if "retention_days" in stored:
                out["retention_days"] = int(stored["retention_days"])
            for key in ("audit_retention_days", "task_retention_days", "sync_retention_days"):
                if key in stored:
                    out[key] = int(stored[key])
            if "log_level" in stored and stored["log_level"] in _VALID_LEVELS:
                out["log_level"] = stored["log_level"]
        except Exception:
            pass
    return out


def _save_settings(db: Session, data: dict):
    import json
    row = db.query(SystemSetting).filter(SystemSetting.key == "system_log").first()
    if not row:
        row = SystemSetting(key="system_log", value=json.dumps(data))
        db.add(row)
    else:
        row.value = json.dumps(data)
        row.updated_at = datetime.utcnow()
    db.commit()


# ---- API ----

@router.get("/api/logs")
def list_logs(
    level: Optional[str] = None,
    category: Optional[str] = None,
    device_id: Optional[int] = None,
    q: Optional[str] = Query(None, description="Search in action/message"),
    since_hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List recent log entries with basic filters.

    Defaults to the last 24 hours. Operators usually want the recent stuff;
    older entries are still in the DB but capped at 500 per request.
    """
    settings = _get_settings(db)
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    query = db.query(DataLog).filter(DataLog.timestamp >= cutoff)

    if settings.get("log_level") == "ERROR":
        query = query.filter(DataLog.level.in_(["ERROR", "WARN"]))
    elif settings.get("log_level") == "WARN":
        query = query.filter(DataLog.level != "DEBUG")

    if level and level.upper() in _VALID_LEVELS:
        query = query.filter(DataLog.level == level.upper())
    if category:
        query = query.filter(DataLog.category == category)
    if device_id:
        query = query.filter(DataLog.device_id == device_id)
    if q:
        like = f"%{q}%"
        query = query.filter((DataLog.action.like(like)) | (DataLog.message.like(like)))

    total = query.count()
    rows = (
        query.order_by(DataLog.timestamp.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "level": r.level,
                "category": r.category,
                "device_id": r.device_id,
                "device_name": r.device_name,
                "device_ip": r.device_ip,
                "action": r.action,
                "message": r.message,
                "duration_ms": (r.detail or {}).get("duration_ms") if r.detail else None,
                "has_raw": bool(r.raw_output),
                "error": r.error,
            }
            for r in rows
        ],
    }


@router.get("/api/logs/audit")
def list_audit_logs(
    request: Request,
    username: Optional[str] = Query(None, max_length=100),
    action: Optional[str] = Query(None, max_length=100),
    success: Optional[bool] = None,
    since_hours: int = Query(168, ge=1, le=24 * 365),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List management audit records. Superusers only."""
    user = getattr(request.state, "user", None)
    if not user or not user.is_superuser:
        raise HTTPException(status_code=403, detail="仅超级管理员可查看审计日志")
    query = db.query(AuditLog).filter(
        AuditLog.timestamp >= datetime.utcnow() - timedelta(hours=since_hours)
    )
    if username:
        query = query.filter(AuditLog.username == username)
    if action:
        query = query.filter(AuditLog.action == action)
    if success is not None:
        query = query.filter(AuditLog.success == success)
    total = query.count()
    rows = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "user_id": row.user_id,
                "username": row.username,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "method": row.method,
                "path": row.path,
                "status_code": row.status_code,
                "success": row.success,
                "client_ip": row.client_ip,
                "detail": row.detail or {},
            }
            for row in rows
        ],
    }


@router.get("/api/logs/{log_id:int}")
def get_log_detail(log_id: int, db: Session = Depends(get_db)):
    row = db.query(DataLog).filter(DataLog.id == log_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "level": row.level,
        "category": row.category,
        "device_id": row.device_id,
        "device_name": row.device_name,
        "device_ip": row.device_ip,
        "action": row.action,
        "message": row.message,
        "raw_output": row.raw_output,
        "detail": row.detail or {},
        "error": row.error,
    }


@router.delete("/api/logs")
def delete_logs(
    before: Optional[str] = Query(None, description="ISO timestamp; delete older entries"),
    level: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Delete log entries. If ``before`` is given, delete entries older than it;
    otherwise delete all entries matching the level filter. Returns count."""
    q = db.query(DataLog)
    if before:
        try:
            cutoff = datetime.fromisoformat(before.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid 'before' timestamp")
        q = q.filter(DataLog.timestamp < cutoff)
    if level and level.upper() in _VALID_LEVELS:
        q = q.filter(DataLog.level == level.upper())
    n = q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": n}


@router.get("/api/logs/stats")
def logs_stats(db: Session = Depends(get_db)):
    return get_log_stats(db)


@router.get("/api/logs/settings")
def get_logs_settings(db: Session = Depends(get_db)):
    return _get_settings(db)


@router.post("/api/logs/settings")
def update_logs_settings(payload: LogSettingsRequest, db: Session = Depends(get_db)):
    current = _get_settings(db)
    if payload.retention_days is not None:
        current["retention_days"] = payload.retention_days
    if payload.audit_retention_days is not None:
        current["audit_retention_days"] = payload.audit_retention_days
    if payload.task_retention_days is not None:
        current["task_retention_days"] = payload.task_retention_days
    if payload.sync_retention_days is not None:
        current["sync_retention_days"] = payload.sync_retention_days
    if payload.log_level is not None:
        current["log_level"] = payload.log_level
    _save_settings(db, current)
    pruned = prune_operational_history(
        db, data_days=current["retention_days"],
        audit_days=current["audit_retention_days"],
        task_days=current["task_retention_days"],
        sync_days=current["sync_retention_days"],
    )
    return {"settings": current, "pruned": pruned}


# ---- Page ----

@router.get("/system/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "logs.html", {
        "app_name": settings.APP_NAME,
        "settings": _get_settings(db),
    })
