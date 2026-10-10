"""Neighbor discovery service using CDP and LLDP."""
import re
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session

from app.models import Device, DeviceGroup, Neighbor, TaskLog, CredentialProfile
from app.services.ssh_service import SSHService, parse_cdp_neighbors, parse_lldp_neighbors
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


# ---- Device-type display names (also used as auto-group names) ----
DEVICE_TYPE_DISPLAY = {
    "cisco_ios": "Cisco IOS 交换机",
    "cisco_ios_xe": "Cisco IOS-XE 交换机",
    "cisco_nxos": "Cisco NX-OS 交换机",
    "cisco_wlc": "Cisco WLC 控制器",
    "cisco_ap": "Cisco AP 无线AP",
}


def classify_neighbor(platform: str, capability: str = "", name: str = "") -> str:
    text = f"{platform} {capability} {name}".upper()
    if re.search(r"IP[ -]?PHONE|TELEPHONE|\bPHONE\b|\bCP-\d|\bSEP[0-9A-F]{12}\b", text):
        return "IPT 电话"
    if re.search(r"\bVG[ -]?\d|VOICE GATEWAY|语音网关", text):
        return "VG 语音网关"
    if re.search(r"AIR-(?:AP|CAP|LAP)|\bC9[01]\d\dAX|ACCESS POINT|\bWLAN ACCESS POINT\b", text):
        return "AP 无线接入点"
    if re.search(r"AIR-CT|\bWLC\b|CONTROLLER", text):
        return "无线控制器"
    if re.search(r"SWITCH|CATALYST|NEXUS|WS-C|\bC9[234569]\d\d|\bN[3579]K", text):
        return "交换机"
    if re.search(r"ROUTER|\bISR|\bASR", text):
        return "路由器"
    return "待识别设备"


def _get_or_create_group_by_device_type(device_type: str, db: Session, category: str = "") -> DeviceGroup:
    """Find or create a device group whose name matches the device type.

    If a group with the display name already exists, reuse it.
    Otherwise create a new one with a descriptive name.
    """
    display_name = category or DEVICE_TYPE_DISPLAY.get(device_type, f"设备类型: {device_type}")
    group = db.query(DeviceGroup).filter(DeviceGroup.name == display_name).first()
    if not group:
        group = DeviceGroup(
            name=display_name,
            description=f"自动创建 - 基于 CDP/LLDP 平台推断的 {device_type} 设备",
        )
        db.add(group)
        db.flush()
        logger.info(f"Auto-created device group: {display_name}")
    return group


def infer_device_type_from_platform(platform_str: str) -> str:
    """Infer device_type from CDP/LLDP platform string.

    Common CDP platform examples:
      - 'Cisco WS-C2960S-24TS-L'  -> cisco_ios
      - 'Cisco Catalyst 9300'      -> cisco_ios_xe
      - 'cisco Nexus C9300'        -> cisco_nxos
      - 'Cisco AIR-CT5508-K9'      -> cisco_wlc
      - 'Cisco AIR-AP1852'         -> cisco_ap
    """
    p = (platform_str or "").upper()
    if not p:
        return ""
    if classify_neighbor(p) == "AP 无线接入点":
        return "cisco_ap"
    # WLC / Controller
    if "AIR-CT" in p or "AIR-AP" in p or "CT5508" in p or "CT5520" in p or "WLC" in p or "CONTROLLER" in p:
        if "AP" in p and "CT" not in p:
            return "cisco_ap"
        return "cisco_wlc"
    # Nexus / NX-OS
    if "NEXUS" in p or "N9K" in p or "N7K" in p or "N5K" in p or "N3K" in p:
        return "cisco_nxos"
    # Catalyst 9k series -> IOS-XE
    if "CATALYST 9" in p or "C9300" in p or "C9500" in p or "C9200" in p or "C9400" in p:
        return "cisco_ios_xe"
    # Default: IOS
    if "CISCO" in p or "WS-C" in p or "CATALYST" in p:
        return "cisco_ios"
    return ""


def discover_device_neighbors(device: Device, db: Session) -> dict:
    """
    Discover CDP and LLDP neighbors for a single device.

    Returns:
    {
        "success": bool,
        "device_id": int,
        "device_name": str,
        "cdp_count": int,
        "lldp_count": int,
        "error": str,
    }
    """
    result = {
        "success": False,
        "device_id": device.id,
        "device_name": device.name,
        "cdp_count": 0,
        "lldp_count": 0,
        "error": "",
    }

    ssh = SSHService(device)
    try:
        if not ssh.connect():
            result["error"] = ssh.last_error or f"SSH connection failed to {device.ip_address}"
            device.status = "offline"
            db.commit()
            return result

        # Delete old neighbor records for this device
        db.query(Neighbor).filter(Neighbor.device_id == device.id).delete()

        all_neighbors = []

        # Try CDP first
        cdp_output = ssh.get_cdp_neighbors()
        cdp_neighbors = parse_cdp_neighbors(cdp_output)
        result["cdp_count"] = len(cdp_neighbors)

        for n in cdp_neighbors:
            neighbor = Neighbor(
                device_id=device.id,
                protocol="cdp",
                local_interface=n.get("local_interface", ""),
                neighbor_name=n.get("neighbor_name", ""),
                neighbor_ip=n.get("neighbor_ip", ""),
                neighbor_interface=n.get("neighbor_interface", ""),
                neighbor_platform=n.get("neighbor_platform", ""),
                neighbor_capability=n.get("neighbor_capability", ""),
            )
            all_neighbors.append(neighbor)

        # Try LLDP
        lldp_output = ssh.get_lldp_neighbors()
        lldp_neighbors = parse_lldp_neighbors(lldp_output)
        result["lldp_count"] = len(lldp_neighbors)

        for n in lldp_neighbors:
            neighbor = Neighbor(
                device_id=device.id,
                protocol="lldp",
                local_interface=n.get("local_interface", ""),
                neighbor_name=n.get("neighbor_name", ""),
                neighbor_ip=n.get("neighbor_ip", ""),
                neighbor_interface=n.get("neighbor_interface", ""),
                neighbor_platform=n.get("neighbor_platform", ""),
                neighbor_capability=n.get("neighbor_capability", ""),
            )
            all_neighbors.append(neighbor)

        # Try to link neighbors to known devices by IP or name
        for neighbor in all_neighbors:
            if neighbor.neighbor_ip:
                matched = db.query(Device).filter(
                    Device.ip_address == neighbor.neighbor_ip
                ).first()
                if matched:
                    neighbor.neighbor_device_id = matched.id
            if not neighbor.neighbor_device_id and neighbor.neighbor_name:
                matched = db.query(Device).filter(
                    Device.name.ilike(f"%{neighbor.neighbor_name}%")
                ).first()
                if matched:
                    neighbor.neighbor_device_id = matched.id

        db.add_all(all_neighbors)

        device.last_discovery = datetime.utcnow()
        device.last_seen = datetime.utcnow()
        device.status = "online"
        db.commit()

        result["success"] = True
        logger.info(
            f"Discovery for {device.name}: {result['cdp_count']} CDP, {result['lldp_count']} LLDP"
        )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Discovery failed for {device.name}: {e}")
        db.rollback()
    finally:
        ssh.disconnect()

    return result


def auto_add_discovered_neighbors(device_ids: list, db: Session, neighbor_ids: list | None = None) -> dict:
    """Auto-add discovered neighbor devices to the device list.

    For each neighbor with a neighbor_ip that is not already a managed device:
    1. Infer device_type from the neighbor's platform string.
    2. Look up a CredentialProfile matching that device_type. If found, use
       the profile's username/password/enable_password/port/source_ip.
       Otherwise, fall back to the source device's credentials.
    3. Auto-assign the new device to a group based on device_type (creating
       the group if it doesn't exist, e.g. "Cisco IOS 交换机").
    4. Use neighbor_name as the device name (or the IP if name is empty).
    5. Link the Neighbor record to the newly created Device.

    Returns:
        {
            "added_count": int,
            "skipped_count": int,
            "added_devices": [{name, ip, device_type, source_device, credential_source, group}],
            "errors": [str, ...],
        }
    """
    result = {
        "added_count": 0,
        "skipped_count": 0,
        "added_devices": [],
        "errors": [],
    }

    # Pre-load all credential profiles indexed by device_type for quick lookup
    all_profiles = db.query(CredentialProfile).all()
    profile_by_type: dict[str, CredentialProfile] = {}
    for p in all_profiles:
        # First profile for each device_type wins
        if p.device_type not in profile_by_type:
            profile_by_type[p.device_type] = p
    logger.info(f"Loaded {len(all_profiles)} credential profiles for {len(profile_by_type)} device types")

    # Cache auto-created groups so we don't query the DB repeatedly
    group_cache: dict[str, DeviceGroup] = {}

    # Gather all neighbors discovered by the selected source devices that
    # have an IP address and are not yet linked to a managed device.
    neighbors = (
        db.query(Neighbor)
        .filter(
            Neighbor.device_id.in_(device_ids),
            Neighbor.neighbor_ip != "",
            Neighbor.neighbor_ip.isnot(None),
        )
        .all()
    )

    # Deduplicate by neighbor_ip — keep the first occurrence
    seen_ips = set()
    unique_neighbors = []
    for n in neighbors:
        if neighbor_ids is not None and n.id not in neighbor_ids:
            continue
        ip = (n.neighbor_ip or "").strip()
        if ip and ip not in seen_ips:
            seen_ips.add(ip)
            unique_neighbors.append(n)

    for neighbor in unique_neighbors:
        ip = (neighbor.neighbor_ip or "").strip()
        if not ip:
            continue

        # Skip if a device with this IP already exists
        from app.models import DeviceIP
        from ipaddress import ip_address
        try:
            address = ip_address(ip)
            if address.is_unspecified or address.is_multicast or address.is_loopback:
                raise ValueError("不可用的管理地址")
        except ValueError:
            result["errors"].append(f"跳过无效管理地址: {ip}")
            continue
        existing = db.query(Device).filter((Device.ip_address == ip) | Device.extra_ips.any(DeviceIP.ip_address == ip)).first()
        if existing:
            # Link the neighbor record to the existing device if not linked
            if not neighbor.neighbor_device_id:
                neighbor.neighbor_device_id = existing.id
            result["skipped_count"] += 1
            continue

        # Get the source device (the one that discovered this neighbor)
        source_device = db.query(Device).filter(Device.id == neighbor.device_id).first()
        if not source_device:
            result["skipped_count"] += 1
            continue

        # Determine device name
        name = (neighbor.neighbor_name or "").strip()
        if not name:
            name = ip

        # Truncate name to fit column (200 chars)
        if len(name) > 190:
            name = name[:190]

        # Infer device type from platform, fall back to source device's type
        device_type = infer_device_type_from_platform(neighbor.neighbor_platform)
        if not device_type:
            device_type = source_device.device_type

        # ---- Credential resolution: profile > source device ----
        credential_source = "source_device"
        profile = profile_by_type.get(device_type)
        if profile:
            credential_source = f"profile:{profile.name}"
            username = profile.username
            port = profile.port
            source_ip = profile.source_ip or ""
            password_enc = profile.password_enc
            enable_password_enc = profile.enable_password_enc
        else:
            # Fall back to source device's credentials
            username = source_device.username
            port = source_device.port
            source_ip = source_device.source_ip or ""
            password_enc = source_device.password_enc
            enable_password_enc = source_device.enable_password_enc

        # ---- Auto-grouping by device type ----
        category = classify_neighbor(neighbor.neighbor_platform, neighbor.neighbor_capability, neighbor.neighbor_name)
        if category not in group_cache:
            group_cache[category] = _get_or_create_group_by_device_type(device_type, db, category)
        group = group_cache[category]
        group_name = group.name

        try:
            new_device = Device(
                name=name,
                ip_address=ip,
                device_type=device_type,
                group_id=group.id,
                company=source_device.company or "",
                model=(neighbor.neighbor_platform or "")[:200],
                function=category,
                username=username,
                port=port,
                source_ip=source_ip,
                is_active=category not in {"IPT 电话", "待识别设备"},
                status="unknown",
            )
            # Copy encrypted credentials directly (avoid decrypt/re-encrypt round-trip)
            new_device.password_enc = password_enc
            new_device.enable_password_enc = enable_password_enc

            db.add(new_device)
            db.flush()  # Get the new device ID

            # Link the neighbor record to the new device
            neighbor.neighbor_device_id = new_device.id

            result["added_count"] += 1
            result["added_devices"].append({
                "id": new_device.id,
                "name": name,
                "ip": ip,
                "device_type": device_type,
                "source_device": source_device.name,
                "credential_source": credential_source,
                "group": group_name,
                "company": source_device.company or "",
            })
            logger.info(
                f"Auto-added device {name} ({ip}) [{device_type}] "
                f"creds={credential_source} group={group_name}"
            )
        except Exception as e:
            result["errors"].append(f"Failed to add {ip}: {str(e)}")
            logger.error(f"Auto-add failed for {ip}: {e}")

    db.commit()
    return result


def _discover_device_thread_safe(device_id: int, device_info: dict) -> dict:
    """Thread-safe discovery: each thread uses its own DB session."""
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {
                "success": False,
                "device_id": device_id,
                "device_name": device_info.get("name", "Unknown"),
                "ip_address": device_info.get("ip_address", ""),
                "error": "Device not found in database",
            }
        return discover_device_neighbors(device, db)
    except Exception as e:
        logger.error(f"Thread discovery error for device {device_id}: {e}", exc_info=True)
        return {
            "success": False,
            "device_id": device_id,
            "device_name": device_info.get("name", "Unknown"),
            "ip_address": device_info.get("ip_address", ""),
            "error": str(e),
        }
    finally:
        db.close()


def _run_discovery_batch(device_ids: list, db: Session) -> dict:
    """Run neighbor discovery on a batch of devices concurrently.

    Returns:
        {"success_count": int, "failed_count": int, "errors": [...], "device_ids": [...]}
    Only devices that exist and are active are discovered. Each device is
    discovered in its own DB session to avoid shared-session threading errors.
    """
    devices = db.query(Device).filter(Device.id.in_(device_ids), Device.is_active == True).all()
    device_infos = {d.id: {"name": d.name, "ip_address": d.ip_address} for d in devices}
    errors = []
    success_count = 0
    failed_count = 0

    max_workers = min(settings.MAX_CONCURRENT_SESSIONS, len(devices)) if devices else 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_device = {
            executor.submit(_discover_device_thread_safe, did, device_infos[did]): did
            for did in device_infos.keys()
        }

        for future in as_completed(future_to_device):
            did = future_to_device[future]
            info = device_infos.get(did, {})
            try:
                result = future.result()
                if result["success"]:
                    success_count += 1
                else:
                    failed_count += 1
                    errors.append({
                        "device_id": did,
                        "device_name": info.get("name", "Unknown"),
                        "ip_address": info.get("ip_address", ""),
                        "error": result["error"],
                    })
            except Exception as e:
                failed_count += 1
                errors.append({
                    "device_id": did,
                    "device_name": info.get("name", "Unknown"),
                    "ip_address": info.get("ip_address", ""),
                    "error": str(e),
                })

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "errors": errors,
        "device_ids": [d.id for d in devices],
    }


def discover_multiple_devices(device_ids: list, db: Session, auto_add: bool = False) -> TaskLog:
    """Run neighbor discovery on multiple devices concurrently.

    If auto_add=True, after discovery completes, newly discovered neighbor
    devices are automatically added to the device list using the source
    device's credentials.
    """
    task = TaskLog(
        task_type="discovery",
        status="running",
        started_at=datetime.utcnow(),
        total_devices=len(device_ids),
    )
    db.add(task)
    db.commit()

    batch = _run_discovery_batch(device_ids, db)
    success_count = batch["success_count"]
    failed_count = batch["failed_count"]
    errors = batch["errors"]

    # Auto-add discovered neighbors as managed devices
    auto_add_result = None
    if auto_add and success_count > 0:
        try:
            auto_add_result = auto_add_discovered_neighbors(device_ids, db)
            logger.info(
                f"Auto-add: {auto_add_result['added_count']} added, "
                f"{auto_add_result['skipped_count']} skipped"
            )
        except Exception as e:
            logger.error(f"Auto-add failed: {e}")
            errors.append({
                "device_id": 0,
                "device_name": "auto-add",
                "ip_address": "",
                "error": f"Auto-add failed: {e}",
            })

    if failed_count == 0:
        status = "success"
    elif success_count == 0:
        status = "failed"
    else:
        status = "partial"

    task.status = status
    task.completed_at = datetime.utcnow()
    task.success_count = success_count
    task.failed_count = failed_count
    task.error_details = errors

    summary_parts = [f"Discovery: {success_count} success, {failed_count} failed, {len(device_ids)} total"]
    if auto_add_result:
        summary_parts.append(
            f"Auto-add: {auto_add_result['added_count']} new devices added, "
            f"{auto_add_result['skipped_count']} already existed"
        )
    task.summary = " | ".join(summary_parts)
    db.commit()

    # Attach auto_add_result to the task object for the API layer to return
    task._auto_add_result = auto_add_result
    return task


def run_full_discovery(seed_ids: list, db: Session, auto_add: bool = True, max_depth: int = 15) -> TaskLog:
    """Recursively crawl the network from the seed devices.

    After discovering a batch, any newly discovered neighbors are auto-added
    as managed devices (using credential profiles / auto-grouping), then those
    new devices are themselves discovered in the next iteration. This produces
    a complete topology rather than just the seed devices' direct neighbors.

    A single TaskLog is created for the whole crawl; per-iteration results are
    aggregated.
    """
    task = TaskLog(
        task_type="discovery",
        status="running",
        started_at=datetime.utcnow(),
        total_devices=len(seed_ids),
    )
    db.add(task)
    db.commit()

    total_success = 0
    total_failed = 0
    all_errors = []
    added_total = 0
    skipped_total = 0
    newly_added_devices = []

    processed = set()
    queue = list(seed_ids)
    depth = 0

    while queue and depth < max_depth:
        # Only discover devices we haven't processed yet
        batch = [did for did in queue if did not in processed]
        queue = []
        if not batch:
            break

        result = _run_discovery_batch(batch, db)
        total_success += result["success_count"]
        total_failed += result["failed_count"]
        all_errors.extend(result["errors"])
        processed.update(batch)

        if auto_add:
            try:
                aa = auto_add_discovered_neighbors(batch, db)
                added_total += aa["added_count"]
                skipped_total += aa["skipped_count"]
                # Queue newly added devices for the next crawl iteration
                for d in aa["added_devices"]:
                    dev = db.query(Device).filter(Device.ip_address == d["ip"]).first()
                    if dev and dev.id not in processed:
                        queue.append(dev.id)
                        newly_added_devices.append(d)
            except Exception as e:
                logger.error(f"Auto-add failed at depth {depth}: {e}")
                all_errors.append({
                    "device_id": 0,
                    "device_name": "auto-add",
                    "ip_address": "",
                    "error": f"Auto-add failed: {e}",
                })

        depth += 1

    # Finalize the aggregated task log
    if total_failed == 0 and total_success > 0:
        status = "success"
    elif total_success == 0:
        status = "failed"
    else:
        status = "partial"

    task.status = status
    task.completed_at = datetime.utcnow()
    task.success_count = total_success
    task.failed_count = total_failed
    task.error_details = all_errors
    task.summary = (
        f"全网递归发现: 已发现 {total_success} 台成功, {total_failed} 台失败, "
        f"遍历 {len(processed)} 台种子/新增设备, 共 {depth} 层; "
        f"自动添加 {added_total} 台新设备, 跳过 {skipped_total} 台已存在"
    )
    db.commit()

    auto_add_result = {
        "added_count": added_total,
        "skipped_count": skipped_total,
        "added_devices": newly_added_devices,
        "recursive": True,
        "depth_reached": depth,
    }
    task._auto_add_result = auto_add_result
    return task
