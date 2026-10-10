"""Network interface detection API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api_models import NICSelectionRequest
from app.database import get_db
from app.models import SystemSetting
from app.services.nic_service import (
    PREFERRED_SOURCE_IP_KEY, get_default_source_ip, get_network_interfaces,
    get_preferred_source_ip,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nics", tags=["network-interfaces"])


@router.get("")
def list_nics(db: Session = Depends(get_db)):
    """List all available network interfaces with IPv4 addresses."""
    interfaces = get_network_interfaces()
    default_ip = get_default_source_ip()
    selected_ip = get_preferred_source_ip(db)
    return {
        "interfaces": interfaces,
        "default_ip": default_ip,
        "selected_ip": selected_ip,
        "selected_available": any(
            row.get("ip_address") == selected_ip and row.get("is_up")
            for row in interfaces
        ) if selected_ip else True,
        "count": len(interfaces),
    }


@router.put("/selection")
def select_default_nic(
    payload: NICSelectionRequest, request: Request, db: Session = Depends(get_db),
):
    source_ip = payload.source_ip
    if source_ip:
        allowed = {
            str(row.get("ip_address") or "")
            for row in get_network_interfaces()
            if row.get("is_up") and not row.get("is_loopback")
        }
        if source_ip not in allowed:
            raise HTTPException(status_code=400, detail="所选 IPv4 不属于本机当前在线网卡")
    row = db.get(SystemSetting, PREFERRED_SOURCE_IP_KEY)
    if row:
        row.value = source_ip
    else:
        db.add(SystemSetting(key=PREFERRED_SOURCE_IP_KEY, value=source_ip))
    db.commit()
    request.state.audit_action = "system.nic.select"
    request.state.audit_resource_type = "network_interface"
    request.state.audit_resource_id = source_ip or "automatic"
    request.state.audit_detail = {"source_ip": source_ip, "mode": "selected" if source_ip else "automatic"}
    return {"ok": True, "selected_ip": source_ip}


@router.get("/default")
def get_default_nic():
    """Get the default outbound IP address."""
    return {"ip": get_default_source_ip()}
