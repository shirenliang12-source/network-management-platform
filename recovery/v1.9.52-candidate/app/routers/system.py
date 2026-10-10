"""Operator-facing runtime, database and upgrade diagnostics."""
from fastapi import APIRouter, HTTPException, Query, Request

from app.config import DATA_DIR, settings
from app.database import get_database_health


router = APIRouter(prefix="/api/system", tags=["system"])


def _require_superuser(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if not user or not user.is_superuser:
        raise HTTPException(status_code=403, detail="仅超级管理员可查看系统健康信息")


@router.get("/health")
def system_health(request: Request, full: bool = Query(False)):
    _require_superuser(request)
    result = get_database_health(full=full)
    result.update({
        "app_version": settings.APP_VERSION,
        "data_dir": str(DATA_DIR),
    })
    return result
