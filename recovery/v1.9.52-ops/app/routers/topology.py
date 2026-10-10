"""Topology API routes."""
import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Device, Neighbor
from app.services.topology_service import build_topology, get_topology_stats, export_topology_graphml, export_topology_drawio
from app.services.discovery_service import discover_multiple_devices, run_full_discovery
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topology", tags=["topology"])


@router.get("")
def get_topology(db: Session = Depends(get_db)):
    """Get the full network topology."""
    return build_topology(db)


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Get topology statistics."""
    return get_topology_stats(db)


@router.post("/discover")
def run_discovery(
    device_ids: List[int] = None,
    auto_add: bool = False,
    recursive: bool = True,
    db: Session = Depends(get_db),
):
    """Run neighbor discovery on specified devices (or all if not specified).

    If auto_add=True, discovered neighbor devices are automatically added
    to the device list using the source device's credentials.

    If recursive=True (default), the discovery crawls the whole reachable
    network from the seed devices (each newly discovered device is then
    itself discovered), producing a complete topology instead of just the
    direct neighbors of the seed devices.
    """
    if device_ids:
        devices = db.query(Device).filter(Device.id.in_(device_ids), Device.is_active == True).all()
    else:
        devices = db.query(Device).filter(Device.is_active == True).all()
        device_ids = [d.id for d in devices]

    if not device_ids:
        raise HTTPException(status_code=404, detail="No active devices found")

    if recursive:
        task = run_full_discovery(device_ids, db, auto_add=auto_add)
    else:
        task = discover_multiple_devices(device_ids, db, auto_add=auto_add)

    response = {
        "task_id": task.id,
        "status": task.status,
        "total_devices": task.total_devices,
        "success_count": task.success_count,
        "failed_count": task.failed_count,
        "summary": task.summary,
        "error_details": task.error_details,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "recursive": recursive,
    }
    # Include auto-add results if available
    auto_add_result = getattr(task, "_auto_add_result", None)
    if auto_add_result:
        response["auto_add"] = auto_add_result
    return response


@router.get("/export/drawio")
def export_drawio(db: Session = Depends(get_db)):
    """Export the topology as a draw.io / diagrams.net (.drawio) file."""
    from fastapi.responses import FileResponse

    filepath = os.path.join(settings.EXPORT_DIR, "network_topology.drawio")
    if export_topology_drawio(db, filepath):
        return FileResponse(
            filepath,
            media_type="application/xml",
            filename="network_topology.drawio",
        )
    raise HTTPException(status_code=500, detail="Failed to export draw.io topology")


@router.get("/neighbors")
def list_neighbors(
    device_id: int = None,
    db: Session = Depends(get_db),
):
    """List all discovered neighbors."""
    query = db.query(Neighbor)
    if device_id:
        query = query.filter(Neighbor.device_id == device_id)
    neighbors = query.order_by(Neighbor.device_id, Neighbor.discovered_at.desc()).all()

    return [
        {
            "id": n.id,
            "device_id": n.device_id,
            "protocol": n.protocol,
            "local_interface": n.local_interface,
            "neighbor_name": n.neighbor_name,
            "neighbor_ip": n.neighbor_ip,
            "neighbor_interface": n.neighbor_interface,
            "neighbor_platform": n.neighbor_platform,
            "neighbor_capability": n.neighbor_capability,
            "neighbor_device_id": n.neighbor_device_id,
            "discovered_at": n.discovered_at,
        }
        for n in neighbors
    ]


@router.get("/export/graphml")
def export_graphml(db: Session = Depends(get_db)):
    """Export topology as GraphML file."""
    from fastapi.responses import FileResponse

    filepath = os.path.join(settings.EXPORT_DIR, "topology.graphml")
    if export_topology_graphml(db, filepath):
        return FileResponse(
            filepath,
            media_type="application/xml",
            filename="network_topology.graphml",
        )
    raise HTTPException(status_code=500, detail="Failed to export topology")
