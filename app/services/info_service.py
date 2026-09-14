"""Device information collection service."""
import logging
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session

from app.models import Device, DeviceInfo, TaskLog
from app.services.ssh_service import (
    SSHService, parse_version, parse_inventory, parse_interfaces
)
from app.services.serial_decoder import decode_serial, extract_production_date_from_outputs
from app.services.log_service import log_event, timed_probe
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def collect_device_info(device: Device, db: Session) -> dict:
    """
    Collect comprehensive device information:
    - Version, model, serial number
    - Hardware inventory
    - Interface status
    - Estimated production date from serial number

    Returns:
    {
        "success": bool,
        "device_id": int,
        "device_name": str,
        "serial_number": str,
        "production_date": str,
        "error": str,
    }
    """
    result = {
        "success": False,
        "device_id": device.id,
        "device_name": device.name,
        "serial_number": "",
        "production_date": "",
        "error": "",
    }

    ssh = SSHService(device)
    started = time.time()
    log_event(
        db, "INFO", "info",
        action="info_collection_start",
        message=f"开始采集 {device.name} ({device.ip_address})",
        device_id=device.id, device_name=device.name, device_ip=device.ip_address,
        detail={"device_type": device.device_type},
    )
    try:
        if not ssh.connect():
            result["error"] = ssh.last_error or f"SSH connection failed to {device.ip_address}"
            device.status = "offline"
            db.commit()
            log_event(
                db, "ERROR", "ssh",
                action="ssh_connect",
                message=f"SSH 连接失败: {result['error']}",
                device_id=device.id, device_name=device.name, device_ip=device.ip_address,
                error=result["error"],
                detail={"duration_ms": int((time.time() - started) * 1000)},
            )
            return result

        # Collect raw data (each step logged via timed_probe so the operator
        # can see exactly which command produced empty/wrong output).
        with timed_probe(db, "version", device.id, device.name, device.ip_address, "show version") as probe:
            version_output = ssh.get_version()
            probe.set_raw(version_output)
            probe.set_result({"size": len(version_output)})
            if not version_output.strip():
                probe.warn("show version 返回为空")

        with timed_probe(db, "inventory", device.id, device.name, device.ip_address, "show inventory") as probe:
            inventory_output = ssh.get_inventory()
            probe.set_raw(inventory_output)
            probe.set_result({"size": len(inventory_output)})
            if not inventory_output.strip():
                probe.warn("show inventory 返回为空")

        with timed_probe(db, "interface", device.id, device.name, device.ip_address, "show ip interface brief") as probe:
            interfaces_output = ssh.get_interfaces()
            probe.set_raw(interfaces_output)
            probe.set_result({"size": len(interfaces_output)})
            if not interfaces_output.strip():
                probe.warn("show ip interface brief 返回为空 - 接口数据可能不全")

        with timed_probe(db, "cpu", device.id, device.name, device.ip_address, "show processes cpu") as probe:
            cpu_output = ssh.get_cpu()
            probe.set_raw(cpu_output)
            cpu_info = parse_cpu(cpu_output)
            probe.set_result(cpu_info)
            if not cpu_info.get("summary"):
                probe.warn(f"CPU 解析失败，原始输出前 200 字符: {cpu_output[:200]!r}")

        with timed_probe(db, "memory", device.id, device.name, device.ip_address, "show processes memory") as probe:
            memory_output = ssh.get_memory()
            probe.set_raw(memory_output)
            memory_info = parse_memory(memory_output)
            probe.set_result(memory_info)
            if not memory_info.get("summary"):
                probe.warn(f"内存解析失败，原始输出前 200 字符: {memory_output[:200]!r}")

        # Parse version info
        version_info = parse_version(version_output)
        from app.services.firewall_parser import parse_firewall_version
        from app.services.command_config import resolve_device_driver
        version_info.update(parse_firewall_version(version_output, resolve_device_driver(device.device_type)))

        # Parse inventory
        inventory_items = parse_inventory(inventory_output)

        # Parse interfaces
        interfaces = parse_interfaces(interfaces_output)

        # Compute interface up/down counts from parsed list
        up_count = sum(1 for i in interfaces if i.get("status", "").lower() == "up")
        down_count = sum(1 for i in interfaces if i.get("status", "").lower() in ("down", "administratively down"))
        if interfaces_output.strip() and not interfaces:
            log_event(
                db, "WARN", "interface",
                action="parse_interfaces",
                message=f"show ip interface brief 返回 {len(interfaces_output.splitlines())} 行但解析得到 0 条接口 - 格式可能不匹配",
                device_id=device.id, device_name=device.name, device_ip=device.ip_address,
                raw_output=interfaces_output[:1500],
            )

        # Get chassis serial from inventory if not in version
        serial_number = version_info.get("serial_number", "")
        model = version_info.get("model", "")

        # Try to get chassis serial from inventory
        for item in inventory_items:
            if "chassis" in item.get("name", "").lower():
                if item.get("serial"):
                    serial_number = item["serial"]
                if item.get("pid"):
                    model = item["pid"]

        # Decode production date from serial number
        production_date = ""
        production_date_source = ""   # "serial" / "auto" / "manual"
        production_date_raw_match = ""
        production_date_pattern = ""

        if serial_number:
            decode_result = decode_serial(serial_number)
            serial_date = decode_result.get("production_date", "")
            if serial_date:
                production_date = serial_date
                production_date_source = "serial"

        if not production_date:
            # Fall back to scanning all probe outputs (show inventory, show diag,
            # etc.) for explicit manufacturing-date lines. This is the only way
            # to get reliable production dates out of modern Cisco devices.
            probed = extract_production_date_from_outputs([
                {"name": "show version",     "output": version_output},
                {"name": "show inventory",   "output": inventory_output},
                {"name": "show interfaces",  "output": interfaces_output},
                {"name": "show cpu",         "output": cpu_output},
                {"name": "show memory",      "output": memory_output},
            ])
            if probed and probed.get("date"):
                production_date = probed["date"]
                production_date_source = "auto"
                production_date_raw_match = probed.get("raw_match", "")
                production_date_pattern = probed.get("pattern", "")
                log_event(
                    db, "INFO", "info",
                    action="production_date",
                    message=(f"自动识别出厂日期: {production_date} "
                             f"(来源: {probed.get('source','?')}, 模式: {probed.get('pattern','?')})"),
                    device_id=device.id, device_name=device.name, device_ip=device.ip_address,
                    detail={
                        "date": production_date,
                        "source_blob": probed.get("source"),
                        "pattern": probed.get("pattern"),
                        "raw_match": probed.get("raw_match"),
                    },
                )

        # Manual override always wins (and we log what we replaced, if anything).
        manual_override = (device.production_date_manual or "").strip()
        if manual_override:
            if production_date and production_date != manual_override:
                log_event(
                    db, "INFO", "info",
                    action="production_date_override",
                    message=(f"自动识别 {production_date} ({production_date_source}) 被手动值 {manual_override} 覆盖"),
                    device_id=device.id, device_name=device.name, device_ip=device.ip_address,
                )
            production_date = manual_override
            production_date_source = "manual"
        elif not production_date:
            log_event(
                db, "INFO", "info",
                action="production_date",
                message="序列号与所有 show 输出均未识别到出厂日期，请到设备详情页手动填写",
                device_id=device.id, device_name=device.name, device_ip=device.ip_address,
            )

        # Build raw data for reference
        raw_data = {
            "version_output": version_output[:5000],  # Limit stored size
            "inventory_output": inventory_output[:5000],
            "inventory_items": inventory_items,
            "cpu_output": cpu_output[:2000],
            "memory_output": memory_output[:2000],
        }

        # Save to database
        info = DeviceInfo(
            device_id=device.id,
            hostname=version_info.get("hostname", device.name),
            vendor="Cisco",
            model=model,
            os_type=version_info.get("os_type", ""),
            os_version=version_info.get("os_version", ""),
            serial_number=serial_number,
            production_date=production_date,
            production_date_source=production_date_source,
            production_date_pattern=production_date_pattern,
            production_date_raw_match=production_date_raw_match,
            uptime=version_info.get("uptime", ""),
            uptime_seconds=version_info.get("uptime_seconds", 0),
            cpu_usage=cpu_info.get("summary", ""),
            memory_usage=memory_info.get("summary", ""),
            interface_up_count=up_count,
            interface_down_count=down_count,
            management_ip=device.ip_address,
            interfaces=interfaces,
            raw_data=raw_data,
            collected_at=datetime.utcnow(),
        )
        db.add(info)

        device.last_info_collection = datetime.utcnow()
        device.last_seen = datetime.utcnow()
        device.status = "online"
        db.commit()

        result["success"] = True
        result["serial_number"] = serial_number
        result["production_date"] = production_date
        result["production_date_source"] = production_date_source
        result["production_date_pattern"] = production_date_pattern
        result["cpu_usage"] = cpu_info.get("summary", "")
        result["memory_usage"] = memory_info.get("summary", "")
        result["interface_up_count"] = up_count
        result["interface_down_count"] = down_count
        logger.info(f"Info collected for {device.name}: model={model}, serial={serial_number}, prod_date={production_date}")

        log_event(
            db, "INFO", "info",
            action="info_collection_done",
            message=(f"采集完成: model={model}, serial={serial_number}, "
                     f"cpu={cpu_info.get('summary') or '-'}, "
                     f"mem={memory_info.get('summary') or '-'}, "
                     f"up={up_count}, down={down_count}"),
            device_id=device.id, device_name=device.name, device_ip=device.ip_address,
            detail={
                "duration_ms": int((time.time() - started) * 1000),
                "model": model, "serial": serial_number,
                "cpu": cpu_info, "memory": memory_info,
                "interface_up_count": up_count, "interface_down_count": down_count,
            },
        )

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Info collection failed for {device.name}: {e}")
        log_event(
            db, "ERROR", "info",
            action="info_collection_failed",
            message=f"采集异常: {e}",
            device_id=device.id, device_name=device.name, device_ip=device.ip_address,
            error=f"{type(e).__name__}: {e}",
            detail={"duration_ms": int((time.time() - started) * 1000)},
        )
        db.rollback()
    finally:
        ssh.disconnect()

    return result


def _collect_device_thread_safe(device_id: int, device_info: dict) -> dict:
    """Thread-safe info collection: each thread uses its own DB session."""
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {
                "success": False,
                "device_id": device_id,
                "device_name": device_info.get("name", "Unknown"),
                "serial_number": "",
                "production_date": "",
                "error": "Device not found in database",
            }
        return collect_device_info(device, db)
    except Exception as e:
        logger.error(f"Thread info collection error for device {device_id}: {e}", exc_info=True)
        return {
            "success": False,
            "device_id": device_id,
            "device_name": device_info.get("name", "Unknown"),
            "serial_number": "",
            "production_date": "",
            "error": str(e),
        }
    finally:
        db.close()


def collect_multiple_devices_info(device_ids: list, db: Session) -> TaskLog:
    """Run info collection on multiple devices concurrently.

    Each device is collected in its own DB session (thread-safe), avoiding the
    "session is already in use" errors that occur when a single shared session
    is used across worker threads.
    """
    task = TaskLog(
        task_type="info",
        status="running",
        started_at=datetime.utcnow(),
        total_devices=len(device_ids),
    )
    db.add(task)
    db.commit()

    # Pre-fetch device info (name) in the main thread to avoid lazy loading.
    devices = db.query(Device).filter(Device.id.in_(device_ids), Device.is_active == True).all()
    device_infos = {d.id: {"name": d.name, "ip_address": d.ip_address} for d in devices}

    errors = []
    success_count = 0
    failed_count = 0

    max_workers = min(settings.MAX_CONCURRENT_SESSIONS, len(devices)) if devices else 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_device = {
            executor.submit(_collect_device_thread_safe, did, device_infos[did]): did
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
                        "error": result.get("error", "Unknown error"),
                    })
            except Exception as e:
                failed_count += 1
                errors.append({
                    "device_id": did,
                    "device_name": info.get("name", "Unknown"),
                    "ip_address": info.get("ip_address", ""),
                    "error": str(e),
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
    task.summary = f"Info: {success_count} success, {failed_count} failed, {len(devices)} total"
    db.commit()

    return task


def parse_cpu(raw_output: str) -> dict:
    """Parse CPU utilization output into a summary string.

    Handles IOS / IOS-XE / NX-OS variants, e.g.:
      - "CPU utilization for five seconds (1 minute, 5 minutes): 5%/1%; one minute: 8%; five minutes: 7%"
      - "CPU utilization for five seconds: 4%, one minute: 5%, five minutes: 6%"

    Returns:
        {"summary": "5% / 8% / 7%", "cpu_5sec": 5, "cpu_1min": 8, "cpu_5min": 7}
    """
    result = {"summary": "", "cpu_5sec": 0, "cpu_1min": 0, "cpu_5min": 0}
    if not raw_output:
        return result

    # 5-second utilization. The line reads "CPU utilization for five seconds
    # (...): N%/M%;" on IOS, or "CPU utilization for five seconds: N%, ..." on
    # NX-OS. Match the first percentage after the colon.
    m = re.search(
        r"CPU utilization\s+for five seconds.*?:\s*(\d+)%",
        raw_output,
        re.IGNORECASE,
    )
    if m:
        result["cpu_5sec"] = int(m.group(1))

    # 1-minute and 5-minute utilization (appear later in the line).
    m = re.search(r"one minute:\s*(\d+)%", raw_output, re.IGNORECASE)
    if m:
        result["cpu_1min"] = int(m.group(1))

    m = re.search(r"five minutes:\s*(\d+)%", raw_output, re.IGNORECASE)
    if m:
        result["cpu_5min"] = int(m.group(1))

    if result["cpu_5sec"] or result["cpu_1min"] or result["cpu_5min"]:
        result["summary"] = (
            f"{result['cpu_5sec']}% / {result['cpu_1min']}% / {result['cpu_5min']}%"
        )

    return result


def parse_memory(raw_output: str) -> dict:
    """Parse memory usage output into a summary string.

    Handles multiple platform formats:
      - IOS / IOS-XE: "Processor Pool Total: 540123456 Max: 543210000 Used: 123456789"
      - NX-OS / generic: "Total: 4053800K  Used: 2051234K  Free: 2002566K"
      - WLC / others: "Memory used: X total: Y"

    Returns {"summary": "63%", "used": ..., "total": ..., "percent": 63}.
    """
    result = {"summary": "", "used": 0, "total": 0, "percent": 0}
    if not raw_output:
        return result

    total = used = 0

    # IOS / IOS-XE "Processor Pool Total: X ... Used: Y"
    m = re.search(
        r"Processor Pool Total:\s*(\d+).*?Used:\s*(\d+)",
        raw_output,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        total, used = int(m.group(1)), int(m.group(2))

    # Generic "Total: X (unit) Used: Y (unit)" (NX-OS, WLC, ...)
    if total == 0:
        m = re.search(
            r"Total:\s*(\d+)\s*[KMG]?.*?Used:\s*(\d+)\s*[KMG]?",
            raw_output,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            total, used = int(m.group(1)), int(m.group(2))

    # "Memory used: X total: Y" style
    if total == 0:
        m = re.search(
            r"Memory.*?used:\s*(\d+).*?total:\s*(\d+)",
            raw_output,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            used, total = int(m.group(1)), int(m.group(2))

    if total > 0:
        pct = round(used / total * 100)
        result["used"] = used
        result["total"] = total
        result["percent"] = pct
        result["summary"] = f"{pct}%"

    return result


def _human_bytes(num: int) -> str:
    """Convert a byte count into a human-readable string (KB/MB/GB)."""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return str(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}PB"
