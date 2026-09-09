"""Schedule management API routes."""
import logging
import threading
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import ScheduleConfig, TaskLog
from app.schemas import ScheduleConfigCreate, ScheduleConfigResponse
from app.scheduler.tasks import reload_scheduler, _run_task, get_running_schedule_ids, VALID_TASK_TYPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def _require_integration_permission(request: Request, task_type: str) -> None:
    if task_type not in {"zabbix_sync", "vcenter_sync"}:
        return
    user = getattr(request.state, "user", None)
    if not user or (not user.is_superuser and "integrations" not in (user.get_modules() or [])):
        raise HTTPException(status_code=403, detail="创建或执行平台同步任务还需要平台集成权限")


@router.get("", response_model=List[ScheduleConfigResponse])
def list_schedules(db: Session = Depends(get_db)):
    """List all scheduled tasks, with live running status."""
    running = set(get_running_schedule_ids())
    result = []
    for s in db.query(ScheduleConfig).order_by(ScheduleConfig.task_type, ScheduleConfig.id).all():
        result.append(ScheduleConfigResponse(
            id=s.id,
            task_type=s.task_type,
            cron_expression=s.cron_expression,
            is_enabled=s.is_enabled,
            description=s.description,
            last_run=s.last_run,
            next_run=s.next_run,
            is_running=s.id in running,
            last_status=s.last_run_status,
        ))
    return result


@router.post("", response_model=ScheduleConfigResponse)
def create_schedule(schedule: ScheduleConfigCreate, request: Request, db: Session = Depends(get_db)):
    """Create a new scheduled task (custom schedules allowed)."""
    if schedule.task_type not in VALID_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task_type. Must be one of: {', '.join(sorted(VALID_TASK_TYPES))}"
        )
    _require_integration_permission(request, schedule.task_type)

    parts = schedule.cron_expression.split()
    if len(parts) != 5:
        raise HTTPException(
            status_code=400,
            detail="Cron expression must have 5 fields: minute hour day month day_of_week"
        )

    s = ScheduleConfig(
        task_type=schedule.task_type,
        cron_expression=schedule.cron_expression,
        is_enabled=schedule.is_enabled,
        description=schedule.description,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    reload_scheduler()
    return ScheduleConfigResponse(
        id=s.id, task_type=s.task_type, cron_expression=s.cron_expression,
        is_enabled=s.is_enabled, description=s.description,
        last_run=s.last_run, next_run=s.next_run, is_running=False, last_status=None,
    )


@router.put("/{schedule_id}", response_model=ScheduleConfigResponse)
def update_schedule(schedule_id: int, schedule: ScheduleConfigCreate, request: Request, db: Session = Depends(get_db)):
    """Update a scheduled task."""
    s = db.query(ScheduleConfig).get(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if schedule.task_type not in VALID_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task_type. Must be one of: {', '.join(sorted(VALID_TASK_TYPES))}"
        )
    _require_integration_permission(request, schedule.task_type)

    parts = schedule.cron_expression.split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    s.task_type = schedule.task_type
    s.cron_expression = schedule.cron_expression
    s.is_enabled = schedule.is_enabled
    s.description = schedule.description
    db.commit()
    db.refresh(s)
    reload_scheduler()
    return ScheduleConfigResponse(
        id=s.id, task_type=s.task_type, cron_expression=s.cron_expression,
        is_enabled=s.is_enabled, description=s.description,
        last_run=s.last_run, next_run=s.next_run, is_running=False,
        last_status=s.last_run_status,
    )


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """Delete a scheduled task."""
    s = db.query(ScheduleConfig).get(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(s)
    db.commit()
    reload_scheduler()
    return {"message": "Schedule deleted"}


@router.post("/{schedule_id}/toggle")
def toggle_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """Enable/disable a scheduled task."""
    s = db.query(ScheduleConfig).get(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found")
    s.is_enabled = not s.is_enabled
    db.commit()
    reload_scheduler()
    return {"id": s.id, "is_enabled": s.is_enabled}


@router.post("/{schedule_id}/run")
def run_schedule_now(schedule_id: int, request: Request, db: Session = Depends(get_db)):
    """Trigger the scheduled task immediately (runs in background)."""
    s = db.query(ScheduleConfig).get(schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if s.task_type not in VALID_TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown task type: {s.task_type}")
    _require_integration_permission(request, s.task_type)
    thread = threading.Thread(target=_run_task, args=[schedule_id], daemon=True)
    thread.start()
    return {"message": f"{s.task_type} task started", "task_type": s.task_type}


@router.get("/tasks")
def list_all_tasks(limit: int = 50, db: Session = Depends(get_db)):
    """List all task execution logs."""
    tasks = db.query(TaskLog).order_by(TaskLog.started_at.desc()).limit(limit).all()
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
