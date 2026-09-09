"""Helpers linking device multi-NIC IPs (Device.extra_ips) to IPAM and IP inventory.

Three kinds of association are supported:
  1. 展示联动 (display linkage): expose a device's full IP list (primary + extra)
     wherever a device is referenced.
  2. 双向反查 (reverse lookup): given an IP address in IPAM / IP inventory, resolve
     the owning device even when no explicit device link was set, by matching the
     address against every device IP (primary or extra).
  3. 自动建档 (auto-create): when a device gains extra IPs, automatically register
     them in IPAM (inside a containing prefix) and in IP inventory (as a /32 row),
     so the inventory stays in sync with the device's real interfaces.
"""
import ipaddress
import logging
from sqlalchemy.orm import Session

from app.models import Device, DeviceIP, IPAMIPAddress, IPAMPrefix, IPInventory

logger = logging.getLogger(__name__)

AUTO_MARKER = "设备额外IP(自动建档)"


def device_ip_entries(device: Device) -> list:
    """Return every IP of a device: primary (is_primary=True) + extra DeviceIPs."""
    entries = [{
        "ip": device.ip_address,
        "is_primary": True,
        "interface_name": "",
        "notes": "",
    }]
    for e in (device.extra_ips or []):
        entries.append({
            "ip": e.ip_address,
            "is_primary": bool(e.is_primary),
            "interface_name": e.interface_name or "",
            "notes": e.notes or "",
        })
    return entries


def device_ip_strings(device: Device) -> list:
    return [e["ip"] for e in device_ip_entries(device) if e["ip"]]


def build_ip_to_device_map(db: Session) -> dict:
    """Map every device IP (primary + extra) -> Device, for reverse lookup."""
    m = {}
    for d in db.query(Device).all():
        for e in device_ip_entries(d):
            ip = (e["ip"] or "").strip()
            if ip:
                m[ip] = d
    return m


def _containing_prefix(db: Session, ip_str: str):
    """Return an IPAMPrefix whose network contains ip_str, else None."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except (ValueError, TypeError):
        return None
    for p in db.query(IPAMPrefix).all():
        try:
            if ip_obj in ipaddress.ip_network(p.prefix, strict=False):
                return p
        except (ValueError, TypeError):
            continue
    return None


def _ip_in_network(ip_obj, net_str: str) -> bool:
    net_str = (net_str or "").strip()
    if not net_str:
        return False
    try:
        return ip_obj in ipaddress.ip_network(net_str, strict=False)
    except (ValueError, TypeError):
        pass
    # 支持 "10.0.1.1-10.0.1.255" 区间写法
    if "-" in net_str:
        try:
            lo, hi = net_str.split("-")
            return ipaddress.ip_address(lo.strip()) <= ip_obj <= ipaddress.ip_address(hi.strip())
        except (ValueError, TypeError):
            return False
    return False


def _ip_covered_by_inventory(db: Session, ip_str: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except (ValueError, TypeError):
        return False
    for it in db.query(IPInventory).all():
        if _ip_in_network(ip_obj, it.subnet) or _ip_in_network(ip_obj, it.ip_segment):
            return True
    return False


def sync_device_auto_links(db: Session, device: Device):
    """Sync auto-created IPAM / IP inventory records with a device's extra IPs.

    Removes this device's previously auto-created records that are no longer among
    its extra IPs, then (re)creates records for the current extra IPs:
      * IPAM  : an IPAMIPAddress inside a containing prefix (status=使用中).
      * IP inventory: a /32 row when no existing inventory row already covers it.
    """
    if not device or not device.id:
        return
    extra_ips = [(e.ip_address or "").strip() for e in (device.extra_ips or [])
                 if (e.ip_address or "").strip()]

    # --- remove stale auto records for this device ---
    cond = (IPAMIPAddress.assigned_device_id == device.id) & \
           (IPAMIPAddress.description.like(f"{AUTO_MARKER}%"))
    if extra_ips:
        cond = cond & (~IPAMIPAddress.address.in_(extra_ips))
    db.query(IPAMIPAddress).filter(cond).delete(synchronize_session=False)

    db.query(IPInventory).filter(
        IPInventory.device_id == device.id,
        IPInventory.usage.like(f"{AUTO_MARKER}%"),
    ).delete(synchronize_session=False)

    # --- (re)create current extra IPs ---
    for ip in extra_ips:
        prefix = _containing_prefix(db, ip)
        if prefix:
            exists = db.query(IPAMIPAddress).filter(
                IPAMIPAddress.prefix_id == prefix.id,
                IPAMIPAddress.address == ip,
            ).first()
            if not exists:
                db.add(IPAMIPAddress(
                    prefix_id=prefix.id,
                    address=ip,
                    status="使用中",
                    allocation_type="静态",
                    dns_name="",
                    description=AUTO_MARKER,
                    assigned_device_id=device.id,
                    device_name=device.name,
                    device_model=device.device_type or "",
                    device_ip=ip,
                ))
        if not _ip_covered_by_inventory(db, ip):
            db.add(IPInventory(
                ip_segment=ip,
                subnet=f"{ip}/32",
                mask="255.255.255.255",
                vlan="",
                usage=AUTO_MARKER,
                company="",
                remarks=f"自动建档：设备 {device.name} 的额外IP",
                device_id=device.id,
                asset_sn="",
                network_type="有线",
                firewall="",
                zone_interface_name="",
            ))
    db.commit()
