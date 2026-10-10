"""Configuration backup service."""
import os
import hashlib
import difflib
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session

from app.models import Device, ConfigBackup, TaskLog
from app.services.ssh_service import SSHService
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def backup_device_config(device: Device, db: Session) -> dict:
    """
    Back up a single device's running configuration.

    Returns:
    {
        "success": bool,
        "device_id": int,
        "device_name": str,
        "ip_address": str,
        "is_changed": bool,
        "change_summary": str,
        "error": str,
    }
    """
    result = {
        "success": False,
        "device_id": device.id,
        "device_name": device.name,
        "ip_address": device.ip_address,
        "is_changed": False,
        "change_summary": "",
        "error": "",
    }

    ssh = SSHService(device)
    try:
        if not ssh.connect():
            err_detail = ssh.last_error or f"SSH connection failed to {device.ip_address}"
            result["error"] = err_detail
            # A failed SSH task is not proof of device unreachability.
            db.commit()
            return result

        config_text = ssh.get_running_config()
        if not config_text or len(config_text.strip()) < 50:
            err_detail = ssh.last_error or f"Empty or invalid config received from {device.ip_address}"
            result["error"] = err_detail
            # SSH connected; invalid command output is not an offline signal.
            device.status = "online"
            db.commit()
            return result

        # Compute hash
        config_hash = hashlib.sha256(config_text.encode("utf-8")).hexdigest()

        # Check if config changed since last backup
        last_backup = (
            db.query(ConfigBackup)
            .filter(ConfigBackup.device_id == device.id)
            .order_by(ConfigBackup.backup_time.desc())
            .first()
        )

        is_changed = True
        change_summary = ""
        if last_backup:
            if last_backup.config_hash == config_hash:
                is_changed = False
                change_summary = "No changes since last backup"
            else:
                # Generate diff
                old_lines = last_backup.config_text.splitlines(keepends=True)
                new_lines = config_text.splitlines(keepends=True)
                diff = list(difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"previous ({last_backup.backup_time.strftime('%Y-%m-%d %H:%M')})",
                    tofile=f"current ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                    n=3,
                ))
                change_summary = "".join(diff[:200])  # Limit diff size
                if len(diff) > 200:
                    change_summary += f"\n... ({len(diff) - 200} more lines)"
        else:
            # The first successful capture is the intended starting point, not
            # a configuration drift event requiring operator acknowledgement.
            is_changed = False
            change_summary = "Initial configuration baseline"

        # Save backup to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = device.name.replace(" ", "_").replace("/", "_")
        backup_filename = f"{safe_name}_{timestamp}.cfg"
        backup_filepath = os.path.join(settings.BACKUP_DIR, backup_filename)

        with open(backup_filepath, "w", encoding="utf-8") as f:
            f.write(config_text)

        # Save to database
        backup = ConfigBackup(
            device_id=device.id,
            config_text=config_text,
            config_hash=config_hash,
            backup_time=datetime.utcnow(),
            file_path=backup_filepath,
            is_changed=is_changed,
            change_summary=change_summary,
            backup_size=len(config_text.encode("utf-8")),
            review_status="pending" if is_changed else "ignored",
            is_baseline=last_backup is None,
        )
        db.add(backup)

        # Update device status
        device.last_backup = datetime.utcnow()
        device.last_seen = datetime.utcnow()
        device.status = "online"
        db.commit()

        result["success"] = True
        try:
            prune_old_backups(db, device.id)
        except Exception:
            db.rollback()
            logger.exception('Backup saved but retention cleanup failed')
        result["is_changed"] = is_changed
        result["change_summary"] = change_summary
        logger.info(f"Backup successful for {device.name} (changed: {is_changed})")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Backup failed for {device.name}: {e}", exc_info=True)
        db.rollback()
    finally:
        ssh.disconnect()

    return result


def _backup_device_thread_safe(device_id: int, device_info: dict) -> dict:
    """
    Thread-safe backup: each thread creates its own DB session.
    device_info contains: name, ip_address, device_type, username, password, 
    enable_password, port, source_ip
    """
    # Create a new session for this thread
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
        return backup_device_config(device, db)
    except Exception as e:
        logger.error(f"Thread backup error for device {device_id}: {e}", exc_info=True)
        return {
            "success": False,
            "device_id": device_id,
            "device_name": device_info.get("name", "Unknown"),
            "ip_address": device_info.get("ip_address", ""),
            "error": str(e),
        }
    finally:
        db.close()


def backup_multiple_devices(device_ids: list, db: Session) -> TaskLog:
    """
    Back up multiple devices concurrently.
    Each thread gets its own DB session for thread safety.
    """
    task = TaskLog(
        task_type="backup",
        status="running",
        started_at=datetime.utcnow(),
        total_devices=len(device_ids),
    )
    db.add(task)
    db.commit()

    # Pre-fetch device info in the main thread (avoid lazy loading issues)
    devices = db.query(Device).filter(Device.id.in_(device_ids), Device.is_active == True).all()
    
    # Collect device info for thread-safe access
    device_infos = {}
    for d in devices:
        device_infos[d.id] = {
            "name": d.name,
            "ip_address": d.ip_address,
        }

    errors = []
    success_count = 0
    failed_count = 0

    # Use ThreadPoolExecutor with thread-safe backup function
    max_workers = min(settings.MAX_CONCURRENT_SESSIONS, len(devices)) if devices else 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_device_id = {
            executor.submit(_backup_device_thread_safe, did, device_infos[did]): did
            for did in device_infos.keys()
        }

        for future in as_completed(future_to_device_id):
            did = future_to_device_id[future]
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

    # Determine status
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
    task.summary = f"Backup: {success_count} success, {failed_count} failed, {len(devices)} total"
    db.commit()

    # Apply retention policy (0 = keep all)
    # Retention runs individually after each successful capture above.

    return task


def prune_old_backups(db: Session, device_id=None):
    """
    Delete config backups older than BACKUP_RETENTION_DAYS (0 = disabled).
    Also removes the corresponding .cfg files from disk.
    """
    from pathlib import Path
    from app.services.backup_retention import policy, excess
    days = settings.BACKUP_RETENTION_DAYS
    devices = [device_id] if device_id is not None else [d.id for d in db.query(Device).all()]
    old_rows = []
    for did in devices:
        keep = policy(db, did)
        if keep is not None:
            old_rows.extend(excess(db, did, keep))
        elif days and days > 0:
            cutoff = datetime.utcnow() - timedelta(days=days)
            history = db.query(ConfigBackup).filter_by(device_id=did).order_by(ConfigBackup.backup_time.desc(), ConfigBackup.id.desc()).all()
            old_rows.extend(r for r in history[1:] if r.backup_time < cutoff and not r.is_baseline)
    if not old_rows:
        return
    paths = [row.file_path for row in old_rows if row.file_path]
    for row in old_rows:
        db.delete(row)
    db.commit()
    root = Path(settings.BACKUP_DIR).resolve()
    for path in paths:
        target = Path(path).resolve()
        if root in target.parents and target.suffix.lower() == '.cfg' and not Path(path).is_symlink() and not db.query(ConfigBackup).filter_by(file_path=path).first():
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logger.warning('Failed to remove retired backup file')
    logger.info('Pruned %s backups under configured retention', len(old_rows))


def get_backup_history(device_id: int, db: Session, limit: int = 50) -> list:
    """Get backup history for a device."""
    return (
        db.query(ConfigBackup)
        .filter(ConfigBackup.device_id == device_id)
        .order_by(ConfigBackup.backup_time.desc())
        .limit(limit)
        .all()
    )


def get_backup_diff(backup_id_1: int, backup_id_2: int, db: Session) -> str:
    """Get diff between two backups."""
    backup1 = db.query(ConfigBackup).get(backup_id_1)
    backup2 = db.query(ConfigBackup).get(backup_id_2)

    if not backup1 or not backup2:
        return "One or both backups not found"

    diff = difflib.unified_diff(
        backup1.config_text.splitlines(keepends=True),
        backup2.config_text.splitlines(keepends=True),
        fromfile=f"{backup1.backup_time.strftime('%Y-%m-%d %H:%M')}",
        tofile=f"{backup2.backup_time.strftime('%Y-%m-%d %H:%M')}",
        n=3,
    )
    return "".join(diff)
