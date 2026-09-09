"""Device management API routes."""
import csv
import io
import re
import logging
import threading
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from app.database import get_db, SessionLocal
from app.api_models import BatchApplyProfileRequest
from app.models import Device, DeviceGroup, DeviceInfo, CredentialProfile, DeviceIP
from app.services.device_ip_link import sync_device_auto_links
from app.schemas import (
    DeviceCreate, DeviceUpdate, DeviceResponse,
    DeviceGroupCreate, DeviceGroupResponse,
    DeviceBatchImport, DeviceBatchOperation,
)
from app.services.info_service import collect_device_info
from app.services.ssh_service import SSHService
from app.services.nic_service import get_preferred_source_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/devices", tags=["devices"])


# ---- Multi-IP helpers ----
def _normalize_ips(ips: list) -> list:
    """Deduplicate + drop blanks, preserve order."""
    seen, out = set(), []
    for ip in ips or []:
        ip = (ip or "").strip()
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _ip_used_by_other(db, ip: str, exclude_device_id: int = None) -> bool:
    """Whether `ip` already belongs to another device (primary or extra)."""
    if db.query(Device).filter(Device.ip_address == ip).first():
        dev = db.query(Device).filter(Device.ip_address == ip).first()
        if dev.id != exclude_device_id:
            return True
    row = db.query(DeviceIP).filter(DeviceIP.ip_address == ip).first()
    if row and row.device_id != exclude_device_id:
        return True
    return False


def _set_device_ips(db, device: Device, additional_ips: list):
    """Replace a device's extra IP rows with `additional_ips` (already excludes
    the primary IP). Only IPs not already used by *other* devices are stored."""
    additional_ips = _normalize_ips(additional_ips)
    # keep any that belongs to this device (so we don't drop its own current IPs)
    kept = []
    for ip in additional_ips:
        if ip == device.ip_address:
            continue  # primary is stored on the device itself
        if _ip_used_by_other(db, ip, exclude_device_id=device.id):
            logger.info(f"Skip duplicate extra IP {ip} for device {device.id}")
            continue
        kept.append(ip)
    # clear existing extras then re-add (simple full replace semantics)
    db.query(DeviceIP).filter(DeviceIP.device_id == device.id).delete()
    for ip in kept:
        db.add(DeviceIP(device_id=device.id, ip_address=ip))


def _device_ip_list(device: Device) -> list:
    return [
        {
            "ip_address": ip.ip_address,
            "interface_name": ip.interface_name or "",
            "is_primary": bool(ip.is_primary),
            "notes": ip.notes or "",
        }
        for ip in (device.extra_ips or [])
    ]


# ---- Device Groups ----
@router.get("/groups", response_model=List[DeviceGroupResponse])
def list_groups(db: Session = Depends(get_db)):
    groups = db.query(DeviceGroup).all()
    result = []
    for g in groups:
        result.append(DeviceGroupResponse(
            id=g.id,
            name=g.name,
            description=g.description or "",
            device_count=len(g.devices),
            created_at=g.created_at,
        ))
    return result


@router.post("/groups", response_model=DeviceGroupResponse)
def create_group(group: DeviceGroupCreate, db: Session = Depends(get_db)):
    existing = db.query(DeviceGroup).filter(DeviceGroup.name == group.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Group '{group.name}' already exists")
    g = DeviceGroup(name=group.name, description=group.description)
    db.add(g)
    db.commit()
    db.refresh(g)
    return DeviceGroupResponse(id=g.id, name=g.name, description=g.description, device_count=0, created_at=g.created_at)


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.query(DeviceGroup).get(group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    db.delete(g)
    db.commit()
    return {"message": "Group deleted"}


# ---- Devices ----
@router.get("", response_model=List[DeviceResponse])
def list_devices(
    group_id: Optional[int] = None,
    company: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Device)
    if group_id:
        query = query.filter(Device.group_id == group_id)
    if company:
        query = query.filter(Device.company == company)
    if status:
        query = query.filter(Device.status == status)
    if search:
        query = query.filter(
            (Device.name.ilike(f"%{search}%")) |
            (Device.ip_address.ilike(f"%{search}%")) |
            (Device.company.ilike(f"%{search}%"))
        )
    devices = query.order_by(Device.name).all()

    # Use the shared serializer so every endpoint returns the exact same fields.
    return [_to_response(d) for d in devices]


@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    """List distinct company names with device counts (for company grouping)."""
    rows = (
        db.query(Device.company, func.count(Device.id))
        .group_by(Device.company)
        .all()
    )
    result = []
    for company, count in rows:
        result.append({"company": company or "", "device_count": count})
    return sorted(result, key=lambda x: (x["company"] == "", x["company"]))


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Session = Depends(get_db)):
    d = db.query(Device).get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Device not found")
    return _to_response(d)


@router.post("", response_model=DeviceResponse)
def create_device(device: DeviceCreate, db: Session = Depends(get_db)):
    # Check for duplicate IP
    existing = db.query(Device).filter(Device.ip_address == device.ip_address).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Device with IP {device.ip_address} already exists")

    d = Device(
        name=device.name,
        ip_address=device.ip_address,
        device_type=device.device_type,
        group_id=device.group_id,
        company=device.company or "",
        model=device.model or "",
        function=device.function or "",
        credential_profile_id=device.credential_profile_id,
        username=device.username,
        port=device.port,
        source_ip=device.source_ip or get_preferred_source_ip(db),
        is_active=device.is_active,
    )
    d.set_password(device.password)
    d.set_enable_password(device.enable_password)

    db.add(d)
    db.flush()  # assign device.id before storing extra IPs
    _set_device_ips(db, d, device.additional_ips)
    db.commit()
    db.refresh(d)
    sync_device_auto_links(db, d)
    return _to_response(d)


@router.put("/{device_id}", response_model=DeviceResponse)
def update_device(device_id: int, device: DeviceUpdate, db: Session = Depends(get_db)):
    d = db.query(Device).get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Device not found")

    update_data = device.model_dump(exclude_unset=True)

    # Handle password fields separately
    password = update_data.pop("password", None)
    enable_password = update_data.pop("enable_password", None)
    additional_ips = update_data.pop("additional_ips", None)

    for key, value in update_data.items():
        setattr(d, key, value)

    if password is not None:
        d.set_password(password)
    if enable_password is not None:
        d.set_enable_password(enable_password)

    db.flush()
    if additional_ips is not None:
        _set_device_ips(db, d, additional_ips)

    db.commit()
    db.refresh(d)
    sync_device_auto_links(db, d)
    return _to_response(d)


@router.delete("/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db)):
    d = db.query(Device).get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Device not found")
    # Remove auto-created IPAM / IP inventory records tied to this device
    from app.models import IPAMIPAddress, IPInventory
    db.query(IPAMIPAddress).filter(
        IPAMIPAddress.assigned_device_id == device_id,
        IPAMIPAddress.description.like("设备额外IP(自动建档)%"),
    ).delete(synchronize_session=False)
    db.query(IPInventory).filter(
        IPInventory.device_id == device_id,
        IPInventory.usage.like("设备额外IP(自动建档)%"),
    ).delete(synchronize_session=False)
    db.delete(d)
    db.commit()
    return {"message": "Device deleted"}


@router.post("/batch", response_model=List[DeviceResponse])
def batch_create_devices(batch: DeviceBatchImport, db: Session = Depends(get_db)):
    created = []
    for device_data in batch.devices:
        existing = db.query(Device).filter(Device.ip_address == device_data.ip_address).first()
        if existing:
            continue
        d = Device(
            name=device_data.name,
            ip_address=device_data.ip_address,
            device_type=device_data.device_type,
            group_id=device_data.group_id,
            username=device_data.username,
            port=device_data.port,
            is_active=device_data.is_active,
        )
        d.set_password(device_data.password)
        d.set_enable_password(device_data.enable_password)
        db.add(d)
        db.flush()
        _set_device_ips(db, d, device_data.additional_ips)
        created.append(d)
    db.commit()
    for d in created:
        db.refresh(d)
        sync_device_auto_links(db, d)
    return [_to_response(d) for d in created]


@router.post("/import-csv")
async def import_devices_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import devices from CSV file.
    CSV format: name, ip_address, device_type, username, password, enable_password, port, group_name, company, model, function
    多网卡/多 IP：可用 ip2, ip3, ... ip10 列填写额外 IP（首个 IP 仍用 ip_address 作主管理 IP）。
    """
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    imported_devices = []

    for row_num, row in enumerate(reader, start=2):
        try:
            ip = row.get("ip_address", "").strip()
            name = row.get("name", "").strip()
            if not ip or not name:
                errors.append(f"Row {row_num}: Missing name or IP")
                continue

            # Collect extra IPs from ip2, ip3, ... columns (multi-NIC import)
            extra_by_idx = {}
            for key in row.keys():
                m = re.match(r"^ip(\d+)$", (key or "").strip(), re.IGNORECASE)
                if m and int(m.group(1)) >= 2:
                    val = (row.get(key) or "").strip()
                    if val:
                        extra_by_idx[int(m.group(1))] = val
            extras = [extra_by_idx[n] for n in sorted(extra_by_idx)]

            existing = db.query(Device).filter(Device.ip_address == ip).first()
            if existing:
                skipped += 1
                continue

            group_name = row.get("group_name", "").strip()
            group_id = None
            if group_name:
                group = db.query(DeviceGroup).filter(DeviceGroup.name == group_name).first()
                if not group:
                    group = DeviceGroup(name=group_name, description="")
                    db.add(group)
                    db.flush()
                group_id = group.id

            d = Device(
                name=name,
                ip_address=ip,
                device_type=row.get("device_type", "cisco_ios").strip() or "cisco_ios",
                group_id=group_id,
                company=row.get("company", "").strip(),
                model=row.get("model", "").strip(),
                function=row.get("function", "").strip(),
                username=row.get("username", "admin").strip() or "admin",
                port=int(row.get("port", "22") or 22),
                is_active=True,
            )
            d.set_password(row.get("password", "").strip())
            d.set_enable_password(row.get("enable_password", "").strip())
            db.add(d)
            db.flush()
            _set_device_ips(db, d, extras)
            imported_devices.append(d)
            imported += 1
        except Exception as e:
            errors.append(f"Row {row_num}: {str(e)}")

    db.commit()
    for d in imported_devices:
        db.refresh(d)
        sync_device_auto_links(db, d)

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "message": f"Imported {imported} devices, skipped {skipped} duplicates",
    }


@router.get("/export/csv")
def export_devices_csv(db: Session = Depends(get_db)):
    """Export all devices to CSV."""
    import csv as csv_mod
    from fastapi.responses import StreamingResponse

    devices = db.query(Device).all()
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow(["name", "ip_address", "ip2", "ip3", "ip4", "ip5",
                     "device_type", "username", "port", "group_name", "status", "last_backup"])

    for d in devices:
        extras = [ip.ip_address for ip in (d.extra_ips or [])]
        writer.writerow([
            d.name, d.ip_address,
            extras[0] if len(extras) > 0 else "",
            extras[1] if len(extras) > 1 else "",
            extras[2] if len(extras) > 2 else "",
            extras[3] if len(extras) > 3 else "",
            d.device_type, d.username, d.port,
            d.group.name if d.group else "",
            d.status,
            d.last_backup.strftime("%Y-%m-%d %H:%M") if d.last_backup else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=devices_export.csv"}
    )


@router.get("/{device_id}/info")
def get_device_info(device_id: int, db: Session = Depends(get_db)):
    """Get the latest collected device info.

    The operator can override the production date on the device form, so we
    prefer that manual value when set. The auto-decoded value from the serial
    number is returned as ``production_date_auto`` for reference.
    """
    device = db.query(Device).get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    info = (
        db.query(DeviceInfo)
        .filter(DeviceInfo.device_id == device_id)
        .order_by(DeviceInfo.collected_at.desc())
        .first()
    )
    if not info:
        raise HTTPException(status_code=404, detail="No device info collected yet")

    manual = (device.production_date_manual or "").strip()
    # info.production_date is now already after manual-override logic when the
    # device was last collected, but for older records (collected before
    # v1.9.35) the persisted value never had any manual override applied, so
    # we still need to re-apply the override here.
    auto = (info.production_date or "")
    auto_source = (info.production_date_source or "")

    return {
        "id": info.id,
        "device_id": info.device_id,
        "hostname": info.hostname,
        "vendor": info.vendor,
        "model": info.model,
        "os_type": info.os_type,
        "os_version": info.os_version,
        "serial_number": info.serial_number,
        # Manual override wins (for legacy collected-records that have no
        # source metadata). Expose both so the UI can show the source.
        "production_date": manual or auto,
        "production_date_auto": auto,
        "production_date_manual": manual,
        "production_date_source": (
            "manual" if manual else (auto_source or ("auto" if auto else ""))
        ),
        "production_date_pattern": info.production_date_pattern or "",
        "production_date_raw_match": info.production_date_raw_match or "",
        "uptime": info.uptime,
        "uptime_seconds": info.uptime_seconds,
        "cpu_usage": info.cpu_usage,
        "memory_usage": info.memory_usage,
        "interface_up_count": info.interface_up_count or 0,
        "interface_down_count": info.interface_down_count or 0,
        "management_ip": info.management_ip,
        "mac_address": info.mac_address,
        "interfaces": info.interfaces or [],
        "collected_at": info.collected_at,
        "last_info_collection": device.last_info_collection,
        # Cheap, obvious "is the data fresh?" hint for the UI.
        "age_seconds": (
            int((datetime.utcnow() - device.last_info_collection).total_seconds())
            if device.last_info_collection else None
        ),
    }


def _run_collection_async(device_id: int):
    """Run info collection in a background thread with its own DB session."""
    db = SessionLocal()
    try:
        device = db.query(Device).get(device_id)
        if device:
            collect_device_info(device, db)
    except Exception as e:
        logger.error(f"Async info collection failed for device {device_id}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/{device_id}/collect-info")
def trigger_info_collection(
    device_id: int,
    background: bool = Query(False, description="Run in background (non-blocking)"),
    db: Session = Depends(get_db),
):
    """Trigger info collection for a single device.

    With ``background=true`` (the default for the UI), collection runs in a
    background thread and the request returns immediately so the page stays
    responsive. The UI then polls GET /api/devices/{id}/info to pick up new
    data once ``collected_at`` advances.
    """
    d = db.query(Device).get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Device not found")

    if background:
        # Kick off a daemon thread so the request returns instantly.
        t = threading.Thread(target=_run_collection_async, args=(device_id,),
                             daemon=True, name=f"collect-info-{device_id}")
        t.start()
        return {
            "success": True,
            "background": True,
            "device_id": device_id,
            "device_name": d.name,
            "message": "信息采集已启动（后台执行），请稍候刷新查看结果。",
        }

    # Synchronous (legacy callers): keep blocking behaviour.
    return collect_device_info(d, db)


@router.post("/{device_id}/test-connection")
def test_device_connection(device_id: int, db: Session = Depends(get_db)):
    """Test SSH connectivity to a device and return detailed status."""
    from app.services.ssh_service import SSHService

    d = db.query(Device).get(device_id)
    if not d:
        raise HTTPException(status_code=404, detail="Device not found")

    ssh = SSHService(d)
    try:
        success = ssh.connect()
        if success:
            d.status = "online"
            d.last_seen = datetime.utcnow()
            db.commit()
            return {
                "success": True,
                "message": f"SSH connection successful to {d.ip_address}:{d.port}",
                "source_ip": d.source_ip or "auto",
                "device_type": d.device_type,
            }
        else:
            return {
                "success": False,
                "message": ssh.last_error or "Unknown connection error",
                "source_ip": d.source_ip or "auto",
                "device_type": d.device_type,
            }
    finally:
        ssh.disconnect()


@router.post("/batch-connect")
def batch_connect_devices(payload: DeviceBatchOperation, db: Session = Depends(get_db)):
    """One-click connect: test SSH connectivity to each selected device using its
    own stored credentials. Each device may use a different account/password.

    Returns a per-device report so the UI can show which devices came online.
    """
    results = []
    success_count = 0
    failed_count = 0

    for device_id in payload.device_ids:
        d = db.query(Device).get(device_id)
        if not d:
            failed_count += 1
            results.append({
                "id": device_id,
                "name": "",
                "ip": "",
                "success": False,
                "message": "设备不存在",
                "source_ip": "",
            })
            continue
        ssh = SSHService(d)
        try:
            ok = ssh.connect()
            if ok:
                d.status = "online"
                d.last_seen = datetime.utcnow()
                success_count += 1
                results.append({
                    "id": d.id,
                    "name": d.name,
                    "ip": d.ip_address,
                    "success": True,
                    "message": f"连接成功（{d.username}@{d.ip_address}:{d.port}）",
                    "source_ip": d.source_ip or "auto",
                })
            else:
                failed_count += 1
                results.append({
                    "id": d.id,
                    "name": d.name,
                    "ip": d.ip_address,
                    "success": False,
                    "message": ssh.last_error or "未知连接错误",
                    "source_ip": d.source_ip or "auto",
                })
        except Exception as e:
            failed_count += 1
            results.append({
                "id": d.id,
                "name": d.name,
                "ip": d.ip_address,
                "success": False,
                "message": str(e),
                "source_ip": d.source_ip or "auto",
            })
        finally:
            ssh.disconnect()

    db.commit()
    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "total": len(payload.device_ids),
        "results": results,
    }


@router.post("/batch-apply-profile")
def batch_apply_profile(payload: BatchApplyProfileRequest, db: Session = Depends(get_db)):
    """Apply a credential profile to multiple devices at once.

    Body: {"device_ids": [1, 2, 3], "profile_id": 5}
    The selected profile's username/password/enable_password/port/source_ip are
    copied into every selected device, so each group of devices can use its own
    account. This is the practical way to set *different accounts for different
    devices* in bulk.
    """
    device_ids = payload.device_ids
    profile_id = payload.profile_id

    profile = db.query(CredentialProfile).get(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Credential profile not found")

    updated = 0
    for device_id in device_ids:
        d = db.query(Device).get(device_id)
        if not d:
            continue
        d.username = profile.username
        d.port = profile.port
        d.source_ip = profile.source_ip or ""
        d.credential_profile_id = profile.id
        if profile.password_enc:
            d.password_enc = profile.password_enc
        if profile.enable_password_enc:
            d.enable_password_enc = profile.enable_password_enc
        updated += 1

    db.commit()
    return {
        "updated": updated,
        "profile_id": profile_id,
        "profile_name": profile.name,
        "device_type": profile.device_type,
        "message": f"已将凭据配置「{profile.name}」应用到 {updated} 台设备",
    }


def _to_response(d: Device) -> DeviceResponse:
    return DeviceResponse(
        id=d.id,
        name=d.name,
        ip_address=d.ip_address,
        device_type=d.device_type,
        group_id=d.group_id,
        group_name=d.group.name if d.group else None,
        company=d.company or "",
        model=d.model or "",
        function=d.function or "",
        credential_profile_id=d.credential_profile_id,
        username=d.username,
        port=d.port,
        source_ip=d.source_ip or "",
        is_active=d.is_active,
        status=d.status,
        additional_ips=_device_ip_list(d),
        last_seen=d.last_seen,
        last_backup=d.last_backup,
        last_discovery=d.last_discovery,
        last_info_collection=d.last_info_collection,
        production_date_manual=d.production_date_manual or "",
        created_at=d.created_at,
        updated_at=d.updated_at,
    )
