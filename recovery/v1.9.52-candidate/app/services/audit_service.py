"""Write and query tamper-evident-in-purpose management audit records.

Audit rows deliberately never contain request bodies. Callers may attach a
small detail dictionary; sensitive keys are recursively redacted before the
row is committed in an independent database session.
"""
import logging
from datetime import datetime
from typing import Any, Optional

from app.database import SessionLocal
from app.models import AuditLog


logger = logging.getLogger(__name__)
_SENSITIVE_PARTS = ("password", "secret", "token", "cookie", "authorization", "bindpw")
_MAX_TEXT = 500


def _sanitize(value: Any, key: str = "", depth: int = 0) -> Any:
    if any(part in key.casefold() for part in _SENSITIVE_PARTS):
        return "[REDACTED]"
    if depth >= 4:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        return {str(k)[:100]: _sanitize(v, str(k), depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, key, depth + 1) for item in list(value)[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_TEXT]


def record_audit(
    *,
    action: str,
    username: str = "",
    user_id: Optional[int] = None,
    resource_type: str = "",
    resource_id: Any = "",
    method: str = "",
    path: str = "",
    status_code: int = 0,
    success: bool = True,
    client_ip: str = "",
    detail: Optional[dict] = None,
) -> None:
    """Persist an audit event without ever breaking the caller's operation."""
    try:
        with SessionLocal() as db:
            db.add(AuditLog(
                timestamp=datetime.utcnow(),
                user_id=user_id,
                username=(username or "")[:100],
                action=(action or "unknown")[:100],
                resource_type=(resource_type or "")[:100],
                resource_id=str(resource_id or "")[:100],
                method=(method or "")[:10],
                path=(path or "")[:500],
                status_code=int(status_code or 0),
                success=bool(success),
                client_ip=(client_ip or "")[:100],
                detail=_sanitize(detail or {}),
            ))
            db.commit()
    except Exception as exc:
        logger.error("Audit log write failed: %s", exc)
