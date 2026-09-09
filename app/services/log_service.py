"""System log service — writes diagnostic entries to the DataLog table.

Each entry captures:
  - which device / command was probed
  - raw SSH output (truncated to keep the DB small)
  - parsed result and parser outcome
  - duration and error message (if any)

These rows power the "系统日志" page so operators can see *why* a value was
missing without having to SSH to the device manually.
"""
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog, DataLog, IntegrationSyncRun, SystemSetting, TaskLog


logger = logging.getLogger(__name__)


# Per-entry raw_output truncation. 8 KB is enough to spot a parsing mismatch
# (a few lines of header + first lines of table) without bloating the DB.
_RAW_OUTPUT_MAX = 8000


def _truncate(s: Optional[str]) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) > _RAW_OUTPUT_MAX:
        return s[:_RAW_OUTPUT_MAX] + "\n...[truncated]..."
    return s


def log_event(
    db: Session,
    level: str,
    category: str,
    action: str = "",
    message: str = "",
    device_id: Optional[int] = None,
    device_name: str = "",
    device_ip: str = "",
    raw_output: Optional[str] = None,
    detail: Optional[dict] = None,
    error: str = "",
) -> DataLog:
    """Insert a DataLog row and commit it immediately.

    Commits inside the call so the row is visible to the API/log viewer even
    if the surrounding operation later rolls back (e.g. SSH succeeded but the
    DeviceInfo insert failed).
    """
    try:
        entry = DataLog(
            timestamp=datetime.utcnow(),
            level=level,
            category=category,
            action=action,
            message=message,
            device_id=device_id,
            device_name=device_name or "",
            device_ip=device_ip or "",
            raw_output=_truncate(raw_output),
            detail=detail or {},
            error=error or "",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
    except Exception as e:  # never let logging break the caller's flow
        logger.error(f"DataLog write failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


@contextmanager
def timed_probe(db: Session, category: str, device_id: Optional[int],
                device_name: str, device_ip: str, action: str):
    """Context manager that logs probe start/finish + duration.

    Usage:
        with timed_probe(db, "cpu", device.id, device.name, device.ip_address,
                         "show processes cpu") as probe:
            out = ssh.get_cpu()
            probe.set_raw(out)
            probe.set_result(parse_cpu(out))

    On exception the entry is logged at ERROR level with the traceback.
    """
    start = time.time()
    state = {"raw": "", "result": None, "level": "INFO", "message": "", "error": ""}

    class _Probe:
        def set_raw(self, raw: str):
            state["raw"] = raw or ""

        def set_result(self, result):
            state["result"] = result

        def set_message(self, msg: str):
            state["message"] = msg

        def warn(self, msg: str):
            state["level"] = "WARN"
            state["message"] = msg

    probe = _Probe()
    try:
        yield probe
    except Exception as e:
        state["level"] = "ERROR"
        state["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        duration_ms = int((time.time() - start) * 1000)
        detail = {
            "duration_ms": duration_ms,
            "raw_size": len(state["raw"]),
            "result": state["result"],
        }
        try:
            log_event(
                db,
                level=state["level"],
                category=category,
                action=action,
                message=state["message"] or f"{action} 采集完成",
                device_id=device_id,
                device_name=device_name,
                device_ip=device_ip,
                raw_output=state["raw"],
                detail=detail,
                error=state["error"],
            )
        except Exception:
            pass


def prune_old_logs(db: Session, retention_days: int) -> int:
    """Delete log entries older than retention_days. Returns count deleted."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    deleted = db.query(DataLog).filter(DataLog.timestamp < cutoff).delete(
        synchronize_session=False
    )
    db.commit()
    return deleted


def prune_operational_history(
    db: Session, *, data_days: int, audit_days: int, task_days: int, sync_days: int,
) -> dict[str, int]:
    """Apply bounded retention to every high-growth operational history table."""
    now = datetime.utcnow()
    deleted = {
        "data_logs": db.query(DataLog).filter(
            DataLog.timestamp < now - timedelta(days=max(1, data_days))
        ).delete(synchronize_session=False),
        "audit_logs": db.query(AuditLog).filter(
            AuditLog.timestamp < now - timedelta(days=max(30, audit_days))
        ).delete(synchronize_session=False),
        "task_logs": db.query(TaskLog).filter(
            TaskLog.started_at < now - timedelta(days=max(7, task_days))
        ).delete(synchronize_session=False),
        "sync_runs": db.query(IntegrationSyncRun).filter(
            IntegrationSyncRun.started_at < now - timedelta(days=max(7, sync_days))
        ).delete(synchronize_session=False),
    }
    db.commit()
    return deleted


def prune_configured_history() -> dict[str, int]:
    """Run configured housekeeping at startup without requiring user action."""
    import json
    from app.database import SessionLocal

    defaults = {
        "retention_days": 30, "audit_retention_days": 365,
        "task_retention_days": 180, "sync_retention_days": 180,
    }
    with SessionLocal() as db:
        row = db.get(SystemSetting, "system_log")
        if row and row.value:
            try:
                stored = json.loads(row.value)
                if isinstance(stored, dict):
                    defaults.update({key: int(stored[key]) for key in defaults if key in stored})
            except (TypeError, ValueError):
                pass
        return prune_operational_history(
            db, data_days=defaults["retention_days"],
            audit_days=defaults["audit_retention_days"],
            task_days=defaults["task_retention_days"],
            sync_days=defaults["sync_retention_days"],
        )


def get_log_stats(db: Session) -> dict:
    """Lightweight stats for the logs page header."""
    from sqlalchemy import func
    total = db.query(func.count(DataLog.id)).scalar() or 0
    error_count = db.query(func.count(DataLog.id)).filter(DataLog.level == "ERROR").scalar() or 0
    warn_count = db.query(func.count(DataLog.id)).filter(DataLog.level == "WARN").scalar() or 0
    last_24h = db.query(func.count(DataLog.id)).filter(
        DataLog.timestamp >= datetime.utcnow() - timedelta(hours=24)
    ).scalar() or 0
    return {
        "total": total,
        "errors": error_count,
        "warnings": warn_count,
        "last_24h": last_24h,
    }
