"""IP address inventory management API routes."""
import csv
import io
import ipaddress
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api_models import StrictRequest
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import IPInventory, Device, DeviceInfo
from app.services.device_ip_link import build_ip_to_device_map, device_ip_entries
from app.schemas import (
    IPIInventoryCreate,
    IPIInventoryUpdate,
    IPIInventoryResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ip-inventory", tags=["ip-inventory"])


class MovePayload(StrictRequest):
    direction: str  # "up" or "down"


@router.get("", response_model=list[IPIInventoryResponse])
def list_ip_inventory(
    db: Session = Depends(get_db),
    search: str = Query("", description="搜索 IP段/子网/掩码/VLAN/用途/备注"),
    company: str = Query("", description="按公司筛选"),
):
    """List IP inventory entries, optionally filtered by search text / company.

    Results are ordered by the user-defined ``sort_order`` (ascending), so the
    list reflects the manual ordering the user has set up; ``id`` is used as a
    stable tiebreaker.
    """
    q = db.query(IPInventory)
    if company:
        q = q.filter(IPInventory.company == company)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IPInventory.ip_segment.ilike(like))
            | (IPInventory.subnet.ilike(like))
            | (IPInventory.mask.ilike(like))
            | (IPInventory.vlan.ilike(like))
            | (IPInventory.company.ilike(like))
            | (IPInventory.firewall.ilike(like))
            | (IPInventory.zone_interface_name.ilike(like))
            | (IPInventory.asset_sn.ilike(like))
            | (IPInventory.remarks.ilike(like))
        )
    items = q.order_by(IPInventory.sort_order.asc(), IPInventory.id.asc()).all()
    return [_to_response(item, db) for item in items]


@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    """Return distinct companies with entry counts (for the filter dropdown)."""
    rows = (
        db.query(IPInventory.company, func.count(IPInventory.id))
        .group_by(IPInventory.company)
        .all()
    )
    result = []
    for company, cnt in rows:
        if company:
            result.append({"company": company, "count": cnt})
    result.sort(key=lambda x: x["company"])
    return result


@router.get("/devices")
def list_devices_for_select(db: Session = Depends(get_db)):
    """Lightweight device list for the IP-inventory device-association dropdown.

    Returns id, name, ip_address and the latest known serial_number so the UI
    can offer a device picker and auto-fill the asset serial number.
    """
    devices = db.query(Device).order_by(Device.name).all()
    result = []
    for d in devices:
        info = (
            db.query(DeviceInfo)
            .filter(DeviceInfo.device_id == d.id)
            .order_by(DeviceInfo.collected_at.desc())
            .first()
        )
        result.append({
            "id": d.id,
            "name": d.name,
            "ip_address": d.ip_address,
            "ip_addresses": [e["ip"] for e in device_ip_entries(d)],
            "serial_number": info.serial_number if info else "",
        })
    return result


@router.get("/{item_id}", response_model=IPIInventoryResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(IPInventory).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="IP 条目不存在")
    return _to_response(item, db)


@router.post("", response_model=IPIInventoryResponse)
def create_item(payload: IPIInventoryCreate, db: Session = Depends(get_db)):
    # Default sort_order: when not provided (or <=0) append to the end of the list.
    if payload.sort_order is None or payload.sort_order <= 0:
        max_so = db.query(func.max(IPInventory.sort_order)).scalar() or 0
        sort_order = max_so + 1
    else:
        sort_order = payload.sort_order
    item = IPInventory(
        ip_segment=payload.ip_segment or "",
        subnet=payload.subnet or "",
        mask=payload.mask or "",
        vlan=payload.vlan or "",
        usage=payload.usage or "",
        company=payload.company or "",
        remarks=payload.remarks or "",
        sort_order=sort_order,
        device_id=payload.device_id if payload.device_id else None,
        asset_sn=payload.asset_sn or "",
        network_type=payload.network_type or "有线",
        firewall=payload.firewall or "",
        zone_interface_name=payload.zone_interface_name or "",
    )
    # Auto-fill asset serial from the associated device when not manually provided.
    if payload.device_id and not (payload.asset_sn and payload.asset_sn.strip()):
        item.asset_sn = _device_serial(db, payload.device_id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_response(item, db)


@router.put("/{item_id}", response_model=IPIInventoryResponse)
def update_item(item_id: int, payload: IPIInventoryUpdate, db: Session = Depends(get_db)):
    item = db.query(IPInventory).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="IP 条目不存在")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(item, key, value if value is not None else "")
    # Auto-fill asset serial from the associated device when selected and currently empty.
    if "device_id" in data and data["device_id"]:
        sn = _device_serial(db, data["device_id"])
        provided_sn = data.get("asset_sn")
        sn_empty = (provided_sn is None) or (str(provided_sn).strip() == "")
        if sn_empty and not (item.asset_sn and item.asset_sn.strip()):
            item.asset_sn = sn
    db.commit()
    db.refresh(item)
    return _to_response(item, db)


@router.post("/{item_id}/move", response_model=IPIInventoryResponse)
def move_item(item_id: int, payload: MovePayload, db: Session = Depends(get_db)):
    """Swap this entry's ``sort_order`` with the adjacent neighbor (up/down).

    This lets the user nudge an entry one position without editing the raw
    number. Moving "up" swaps with the next smaller sort_order; "down" swaps
    with the next larger one.
    """
    item = db.query(IPInventory).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="IP 条目不存在")
    direction = (payload.direction or "").lower()
    if direction == "up":
        neighbor = (
            db.query(IPInventory)
            .filter(IPInventory.sort_order < item.sort_order)
            .order_by(IPInventory.sort_order.desc())
            .first()
        )
    elif direction == "down":
        neighbor = (
            db.query(IPInventory)
            .filter(IPInventory.sort_order > item.sort_order)
            .order_by(IPInventory.sort_order.asc())
            .first()
        )
    else:
        raise HTTPException(status_code=400, detail="direction 必须是 up 或 down")
    if not neighbor:
        return _to_response(item, db)  # already at the top/bottom edge
    item.sort_order, neighbor.sort_order = neighbor.sort_order, item.sort_order
    db.commit()
    return _to_response(item, db)


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(IPInventory).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="IP 条目不存在")
    db.delete(item)
    db.commit()
    return {"message": "已删除"}


class ImportPayload(StrictRequest):
    csv: str  # raw CSV text (supports BOM / quoted fields)


# Header aliases -> model field. Keyed by a normalized (lower, stripped) header.
_IMPORT_HEADER_MAP = {
    "firewall": "firewall",
    "subnet": "subnet",
    "company": "company",
    "ip subnet": "ip_segment",
    "ipsubnet": "ip_segment",
    "ip_segment": "ip_segment",
    "ip segment": "ip_segment",
    "mask": "mask",
    "vlan id": "vlan",
    "vlanid": "vlan",
    "vlan": "vlan",
    "zone/interface name": "zone_interface_name",
    "zoneinterface name": "zone_interface_name",
    "zone_interface_name": "zone_interface_name",
    "zone/interface": "zone_interface_name",
    "备注": "remarks",
    "remarks": "remarks",
    "排序": "sort_order",
    "sort_order": "sort_order",
}


@router.post("/import")
def import_csv(payload: ImportPayload, db: Session = Depends(get_db)):
    """Bulk-import IP inventory entries from CSV text.

    The CSV header row is matched (case-insensitively) against known column
    names, so both the exported format and common variants are accepted. Rows
    where none of the core columns carry data are skipped.
    """
    raw = payload.csv or ""
    if not raw.strip():
        raise HTTPException(status_code=400, detail="CSV 内容为空")

    # Strip a leading UTF-8 BOM if present.
    if raw.startswith("\ufeff"):
        raw = raw[1:]

    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader]
    if not rows:
        raise HTTPException(status_code=400, detail="CSV 没有可读的行")

    header = [h.strip() for h in rows[0]]
    col_index = {}
    for idx, h in enumerate(header):
        key = h.lower().replace(" ", "")
        mapped = _IMPORT_HEADER_MAP.get(h.lower().strip()) or _IMPORT_HEADER_MAP.get(key)
        if mapped:
            col_index[mapped] = idx

    if not col_index:
        raise HTTPException(
            status_code=400,
            detail="未识别到任何已知列（需要包含 Firewall/Subnet/Company/IP subnet/Mask/VLAN ID/Zone/Interface Name/备注 等之一）",
        )

    max_so = db.query(func.max(IPInventory.sort_order)).scalar() or 0
    created = 0
    skipped = 0
    errors = []
    for lineno, row in enumerate(rows[1:], start=2):
        if not row or all((c or "").strip() == "" for c in row):
            skipped += 1
            continue
        data = {}
        for field, idx in col_index.items():
            if idx < len(row):
                data[field] = (row[idx] or "").strip()
        # Skip rows that have no usable content at all.
        if not any(data.get(f) for f in ("firewall", "subnet", "ip_segment", "company", "mask", "vlan", "zone_interface_name", "remarks")):
            skipped += 1
            continue
        try:
            sort_order = None
            if data.get("sort_order"):
                try:
                    sort_order = int(data["sort_order"])
                except (ValueError, TypeError):
                    sort_order = None
            if sort_order is None or sort_order <= 0:
                max_so += 1
                sort_order = max_so
            item = IPInventory(
                firewall=data.get("firewall", "") or "",
                subnet=data.get("subnet", "") or "",
                company=data.get("company", "") or "",
                ip_segment=data.get("ip_segment", "") or "",
                mask=data.get("mask", "") or "",
                vlan=data.get("vlan", "") or "",
                zone_interface_name=data.get("zone_interface_name", "") or "",
                remarks=data.get("remarks", "") or "",
                sort_order=sort_order,
                network_type="有线",
            )
            db.add(item)
            created += 1
        except Exception as e:  # pragma: no cover - defensive
            errors.append(f"第 {lineno} 行: {e}")
    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def _device_serial(db: Session, device_id: int) -> str:
    """Return the latest known serial number for a device (from DeviceInfo)."""
    dev = db.query(Device).get(device_id)
    if not dev:
        return ""
    info = (
        db.query(DeviceInfo)
        .filter(DeviceInfo.device_id == dev.id)
        .order_by(DeviceInfo.collected_at.desc())
        .first()
    )
    return info.serial_number if info else ""


def _networks_of(item: IPInventory):
    """Return the parsed networks/ranges covered by an inventory row."""
    out = []
    for field in (item.subnet, item.ip_segment):
        net_str = (field or "").strip()
        if not net_str:
            continue
        try:
            out.append(ipaddress.ip_network(net_str, strict=False))
            continue
        except (ValueError, TypeError):
            pass
        if "-" in net_str:
            try:
                lo, hi = net_str.split("-")
                out.append(("range", ipaddress.ip_address(lo.strip()), ipaddress.ip_address(hi.strip())))
            except (ValueError, TypeError):
                continue
    return out


def _resolve_device_by_network(db: Session, item: IPInventory):
    """双向反查：找到 IP 落在本条网段内的设备（主IP 或 额外IP）。"""
    nets = _networks_of(item)
    if not nets:
        return None
    for ip_str, dev in build_ip_to_device_map(db).items():
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except (ValueError, TypeError):
            continue
        for n in nets:
            if isinstance(n, tuple):
                if n[1] <= ip_obj <= n[2]:
                    return dev
            elif ip_obj in n:
                return dev
    return None


def _to_response(item: IPInventory, db: Session = None) -> IPIInventoryResponse:
    device_name = ""
    device_ips = []
    reverse_linked = False
    dev = db.query(Device).get(item.device_id) if (db is not None and item.device_id) else None
    if dev is None and db is not None:
        dev = _resolve_device_by_network(db, item)
        if dev is not None:
            reverse_linked = True
    if dev is not None:
        device_name = dev.name
        device_ips = device_ip_entries(dev)
    return IPIInventoryResponse(
        id=item.id,
        ip_segment=item.ip_segment or "",
        subnet=item.subnet or "",
        mask=item.mask or "",
        vlan=item.vlan or "",
        usage=item.usage or "",
        company=item.company or "",
        remarks=item.remarks or "",
        sort_order=item.sort_order or 0,
        device_id=item.device_id,
        asset_sn=item.asset_sn or "",
        network_type=item.network_type or "有线",
        firewall=item.firewall or "",
        zone_interface_name=item.zone_interface_name or "",
        device_name=device_name,
        device_ips=device_ips,
        reverse_linked=reverse_linked,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
